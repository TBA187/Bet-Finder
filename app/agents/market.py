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
            return self.report(status="no_data", summary="No bookmaker prices stored for this match.",
                               warnings=["Refresh the data sources or enter odds manually."])
        books = {b["bookmaker"] for c in comps.values() for b in c["books"]}
        sharp = sorted({bk for c in comps.values() for bk in c["consensus"]["sharp_books"]})
        main = main_market(comps)
        mc = comps[main]
        findings = [f"{len(books)} bookmakers across {len(comps)} markets"]
        best_txt = ", ".join(f"{mc['selection_labels'][s]} {mc['best'][s]['odds']:.2f} ({mc['best'][s]['name']})"
                             for s in mc["selections"] if s in mc["best"])
        findings.append(f"Best {mc['label']}: {best_txt}")
        if mc["avg_margin_pct"] is not None:
            low = min((b for b in mc["books"] if b["margin_pct"] is not None), key=lambda b: b["margin_pct"])
            findings.append(f"Average margin {mc['avg_margin_pct']:.1f}%. Lowest: {low['name']} {low['margin_pct']:.1f}%")
        movements = []
        for s in mc["selections"]:
            for v in mc["quotes"].get(s, []):
                if abs(v["move_pct"]) >= 3.0:
                    movements.append({"selection": s, "bookmaker": v["bookmaker"], "name": v["name"], "from": v["opening_odds"],
                                      "to": v["odds"], "move_pct": v["move_pct"], "sharp": v["bookmaker"] in sharp})
        for m in sorted(movements, key=lambda m: (not m["sharp"], -abs(m["move_pct"])))[:4]:
            direction = "shortened" if m["move_pct"] < 0 else "drifted"
            findings.append(f"{m['name']}: "
                            f"{mc['selection_labels'][m['selection']]} {direction} {m['from']:.2f} → {m['to']:.2f} ({m['move_pct']:+.1f}%)")
        arbs = [c["label"] for c in comps.values() if c["arbitrage_pct"]]
        warnings = []
        if arbs:
            warnings.append(f"Cross-bookmaker arbitrage in: {', '.join(arbs)}. That is a strong sign of a stale or wrong price.")
        stale = sorted({b["name"] for c in comps.values() for b in c["books"] if b["stale"]})
        if stale:
            warnings.append(f"Stale prices (not updated recently): {', '.join(stale)}")
        if not sharp:
            warnings.append("No sharp bookmaker or exchange prices. The fair probability is a soft-bookmaker consensus and is less reliable.")
        n_complete = mc["consensus"]["n_books"]
        conf = min(1.0, n_complete / 8) * 0.6 + (0.3 if sharp else 0.0) + 0.1
        if n_complete == 0:
            conf = 0.1
        status = "ok" if n_complete >= 2 else "partial"
        names = {b["bookmaker"]: b["name"] for c in comps.values() for b in c["books"]}
        summary = (f"Consensus from {n_complete} complete bookmaker markets"
                   + (f" anchored on {', '.join(names.get(k, k) for k in sharp)}." if sharp else "."))
        return self.report(status=status, confidence=conf, summary=summary, findings=findings, warnings=warnings,
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
            return self.report(status="no_data", summary="No probability estimate, so value cannot be measured.")
        min_ev = float(ctx.settings.get("min_ev_pct", 3.0))
        for s in sels:
            ev = s["ev_pct"]
            s["value_class"] = ("strong_value" if ev >= max(8.0, 2 * min_ev) else
                                "value" if ev >= min_ev else
                                "marginal" if ev > 0 else "no_value")
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
        findings = [f"{s['label']} @ {s['best']['odds']:.2f} ({s['best']['name']}): EV {s['ev_pct']:+.1f}%, "
                    f"fair {s['fair_odds']:.2f}" for s in value[:5]]
        warnings = [f"Possible pricing error: {s['label']} @ {s['best']['odds']:.2f} at {s['best']['name']}. "
                    "Verify before betting. Bookmakers may void obvious errors (palpable error rules)." for s in errors]
        n_val = sum(1 for s in sels if s["value_class"] in ("value", "strong_value"))
        summary = (f"{n_val} selection(s) priced above the estimated fair odds by at least {min_ev:.0f}% EV." if n_val
                   else "No selection offers meaningful positive expected value.")
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
            return self.report(status="no_data", summary="Nothing to assess.")
        stale_after = float(ctx.settings.get("stale_minutes", 240))
        disagreement = ctx.disagreement or 0.0
        prob_rep = ctx.reports.get("probability")
        conflict = bool(prob_rep and prob_rep.details.get("max_gap_vs_market", 0) > 0.12)
        lineup_unknown = ctx.sport == "football" and ctx.reports.get("lineups") and ctx.reports["lineups"].status == "no_data"
        for s in sels:
            score, factors = 0.0, []
            p, odds = s["final_prob"], s["best"]["odds"]
            if p < 0.25:
                score += 25; factors.append("low win probability (high variance)")
            elif p < 0.35:
                score += 15; factors.append("below 35% win probability")
            elif p < 0.5:
                score += 7
            if odds > 5.0:
                score += 10; factors.append("long odds")
            conf = s.get("confidence", 50)
            if conf < 70:
                score += (70 - conf) * 0.5
                if conf < 50:
                    factors.append("low data confidence")
            n = s.get("n_books_market", 0)
            if n < 3:
                score += 15; factors.append(f"only {n} bookmaker(s) price this market")
            elif n < 5:
                score += 7
            if "possible_pricing_error" in s.get("pricing_flags", []):
                score += 15; factors.append("possible pricing error (void risk)")
            if s["best"]["age_min"] > stale_after:
                score += 10; factors.append("best price is stale")
            if s["best"]["source"] == "manual":
                score += 5; factors.append("manually entered price (unverified)")
            if disagreement > 0.5:
                score += 8; factors.append("agents disagree")
            if conflict:
                score += 10; factors.append("model conflicts with market")
            if lineup_unknown:
                score += 4
            if s.get("prob_source") == "model-only":
                score += 20; factors.append("no market consensus (model only)")
            score = min(100.0, score)
            s["risk_score"] = round(score, 1)
            s["risk_rating"] = rating_for(score)
            s["risk_factors"] = factors
        ratings = [s["risk_rating"] for s in sels if s.get("ev_pct", -1) > 0]
        summary = "Risk assessed per selection from variance, data confidence, market depth and price reliability."
        findings = []
        if disagreement > 0.5:
            findings.append(f"Agent disagreement index {disagreement:.2f}: the evidence points in different directions.")
        if conflict:
            findings.append("The statistical model and the market disagree strongly.")
        if ratings:
            findings.append("Positive-EV selections risk: " + ", ".join(sorted(set(ratings), key=RISK_LEVELS.index)))
        return self.report(status="ok", confidence=0.6, summary=summary, findings=findings,
                           details={"disagreement": disagreement})


def rating_for(score: float) -> str:
    if score < 25:
        return "low"
    if score < 45:
        return "medium"
    if score < 65:
        return "high"
    return "very_high"
