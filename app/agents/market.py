"""Market-side agents: Odds Scanner (1), Value Detection (2), Risk (10)."""
from __future__ import annotations

from app.agents.base import Agent, AnalysisContext
from app.config import RISK_LEVELS

MAIN_MARKETS = ("h2h", "dnb", "h2h3")


def main_market(comparisons: dict) -> str | None:
    for mid in MAIN_MARKETS:
        if mid in comparisons:
            return mid
    return next(iter(comparisons), None)


# --------------------------------------------------------------------------- 1
class OddsScannerAgent(Agent):
    key = "odds_scanner"
    name = "Odds Scanner Agent"
    phase = 1

    def run(self, ctx: AnalysisContext):
        comps = ctx.comparisons
        if not comps:
            return self.report(status="no_data", summary=ctx.t("os_none"), warnings=[ctx.t("os_none_warn")])
        books = {b["bookmaker"] for c in comps.values() for b in c["books"]}
        names = {b["bookmaker"]: b["name"] for c in comps.values() for b in c["books"]}
        sharp = sorted({bk for c in comps.values() for bk in c["consensus"]["sharp_books"]})
        main = main_market(comps)
        mc = comps[main]
        findings = [ctx.t("os_books", n=len(books), m=len(comps))]
        best_txt = ", ".join(f"{mc['selection_labels'][s]} {mc['best'][s]['odds']:.2f} ({mc['best'][s]['name']})"
                             for s in mc["selections"] if s in mc["best"])
        findings.append(ctx.t("os_best", label=mc["label"], txt=best_txt))
        if mc["avg_margin_pct"] is not None:
            low = min((b for b in mc["books"] if b["margin_pct"] is not None), key=lambda b: b["margin_pct"])
            findings.append(ctx.t("os_margin", avg=f"{mc['avg_margin_pct']:.1f}", name=low["name"], low=f"{low['margin_pct']:.1f}"))
        movements = []
        for s in mc["selections"]:
            for v in mc["quotes"].get(s, []):
                if abs(v["move_pct"]) >= 3.0:
                    movements.append({"selection": s, "bookmaker": v["bookmaker"], "name": v["name"], "from": v["opening_odds"],
                                      "to": v["odds"], "move_pct": v["move_pct"], "sharp": v["bookmaker"] in sharp})
        for m in sorted(movements, key=lambda m: (not m["sharp"], -abs(m["move_pct"])))[:4]:
            findings.append(ctx.t("os_move", name=m["name"], sel=mc["selection_labels"][m["selection"]],
                                  dir=ctx.t("os_shortened" if m["move_pct"] < 0 else "os_drifted"),
                                  a=f"{m['from']:.2f}", b=f"{m['to']:.2f}", pct=f"{m['move_pct']:+.1f}%"))
        arbs = [c["label"] for c in comps.values() if c["arbitrage_pct"]]
        warnings = []
        if arbs:
            warnings.append(ctx.t("os_arb", markets=", ".join(arbs)))
        stale = sorted({b["name"] for c in comps.values() for b in c["books"] if b["stale"]})
        if stale:
            warnings.append(ctx.t("os_stale", names=", ".join(stale)))
        if not sharp:
            warnings.append(ctx.t("os_no_sharp"))
        n_complete = mc["consensus"]["n_books"]
        conf = (min(1.0, n_complete / 8) * 0.6 + (0.3 if sharp else 0.0) + 0.1) if n_complete else 0.1
        anchor = ctx.t("os_anchor", names=", ".join(names.get(k, k) for k in sharp)) if sharp else ""
        return self.report(status="ok" if n_complete >= 2 else "partial", confidence=conf,
                           summary=ctx.t("os_summary", n=n_complete, anchor=anchor), findings=findings, warnings=warnings,
                           data_sources=sorted({src for c in comps.values() for b in c["books"] for src in b["source"]}),
                           details={"main_market": main, "n_bookmakers": len(books), "sharp_books": sharp,
                                    "arbitrage_markets": arbs, "movements": movements})


# --------------------------------------------------------------------------- 2
class ValueDetectionAgent(Agent):
    key = "value_detection"
    name = "Value Detection Agent"
    phase = 3

    def run(self, ctx: AnalysisContext):
        sels = [s for s in ctx.selections if s.get("ev_pct") is not None]
        if not sels:
            return self.report(status="no_data", summary=ctx.t("vd_none"))
        min_ev = float(ctx.settings.get("min_ev_pct", 3.0))
        for s in sels:
            ev = s["ev_pct"]
            s["value_class"] = ("strong_value" if ev >= max(8.0, 2 * min_ev) else
                                "value" if ev >= min_ev else "marginal" if ev > 0 else "no_value")
            flags = set(s["best"]["flags"])
            pricing = []
            if "possible_error" in flags:
                pricing.append("possible_pricing_error")
            elif "outlier" in flags:
                pricing.append("outlier_price")
            elif "high_price" in flags:
                pricing.append("above_market")
            if s.get("arbitrage_pct"):
                pricing.append("arbitrage")
            s["pricing_flags"] = pricing
        value = sorted([s for s in sels if s["ev_pct"] > 0], key=lambda s: -s["ev_pct"])
        errors = [s for s in sels if "possible_pricing_error" in s["pricing_flags"]]
        findings = [ctx.t("vd_line", label=s["label"], odds=f"{s['best']['odds']:.2f}", book=s["best"]["name"],
                          ev=f"{s['ev_pct']:+.1f}%", fair=f"{s['fair_odds']:.2f}") for s in value[:5]]
        warnings = [ctx.t("vd_error", label=s["label"], odds=f"{s['best']['odds']:.2f}", book=s["best"]["name"]) for s in errors]
        n_val = sum(1 for s in sels if s["value_class"] in ("value", "strong_value"))
        summary = ctx.t("vd_summary", n=n_val, min=f"{min_ev:g}") if n_val else ctx.t("vd_summary_none")
        return self.report(status="ok", confidence=0.6, summary=summary, findings=findings, warnings=warnings,
                           details={"value_count": n_val, "error_suspects": len(errors)})


# -------------------------------------------------------------------------- 10
class RiskAgent(Agent):
    key = "risk"
    name = "Risk Agent"
    phase = 3

    def run(self, ctx: AnalysisContext):
        sels = [s for s in ctx.selections if s.get("final_prob") is not None]
        if not sels:
            return self.report(status="no_data", summary=ctx.t("risk_none"))
        stale_after = float(ctx.settings.get("stale_minutes", 240))
        disagreement = ctx.disagreement or 0.0
        prob_rep = ctx.reports.get("probability")
        conflict = bool(prob_rep and prob_rep.details.get("max_gap_vs_market", 0) > 0.12)
        lineup_rep = ctx.reports.get("lineups")
        lineup_unknown = ctx.sport == "football" and lineup_rep is not None and lineup_rep.status == "no_data"
        for s in sels:
            score, codes = 0.0, []
            p, odds = s["final_prob"], s["best"]["odds"]
            if p < 0.25:
                score += 25
                codes.append(("rf_low_prob", {}))
            elif p < 0.35:
                score += 15
                codes.append(("rf_below35", {}))
            elif p < 0.5:
                score += 7
            if odds > 5.0:
                score += 10
                codes.append(("rf_long_odds", {}))
            conf = s.get("confidence", 50)
            if conf < 70:
                score += (70 - conf) * 0.5
                if conf < 50:
                    codes.append(("rf_low_conf", {}))
            n = s.get("n_books_market", 0)
            if n < 3:
                score += 15
                codes.append(("rf_few_books", {"n": n}))
            elif n < 5:
                score += 7
            if "possible_pricing_error" in s.get("pricing_flags", []):
                score += 15
                codes.append(("rf_error", {}))
            if s["best"]["age_min"] > stale_after:
                score += 10
                codes.append(("rf_stale", {}))
            if s["best"]["source"] == "manual":
                score += 5
                codes.append(("rf_manual", {}))
            if disagreement > 0.5:
                score += 8
                codes.append(("rf_disagree", {}))
            if conflict:
                score += 10
                codes.append(("rf_conflict", {}))
            if lineup_unknown:
                score += 4
            if s.get("prob_source") == "model-only":
                score += 20
                codes.append(("rf_model_only", {}))
            score = min(100.0, score)
            s["risk_score"] = round(score, 1)
            s["risk_rating"] = rating_for(score)
            s["risk_codes"] = [c for c, _ in codes]
            s["risk_factors"] = [ctx.t(c, **kw) for c, kw in codes]
        ratings = sorted({s["risk_rating"] for s in sels if s.get("ev_pct", -1) > 0}, key=RISK_LEVELS.index)
        findings = []
        if disagreement > 0.5:
            findings.append(ctx.t("risk_disagree", d=f"{disagreement:.2f}"))
        if conflict:
            findings.append(ctx.t("risk_conflict"))
        if ratings:
            findings.append(ctx.t("risk_ratings", r=", ".join(ctx.t(f"risk_{r}") for r in ratings)))
        return self.report(status="ok", confidence=0.6, summary=ctx.t("risk_summary"), findings=findings,
                           details={"disagreement": disagreement})


def rating_for(score: float) -> str:
    if score < 25:
        return "low"
    if score < 45:
        return "medium"
    if score < 65:
        return "high"
    return "very_high"
