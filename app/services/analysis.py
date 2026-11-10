"""Analysis engine: runs the 10 agents for one fixture and combines them.

Combination (per fixture):
1. Bookmaker consensus (margin removed) is the anchor probability.  Betting
   markets are hard to beat, so everything else is a bounded adjustment.
2. The sport model is fitted to the consensus -> theta_market.
3. The Probability Agent's independent model (theta_model) is blended in with
   weight = model_weight x its confidence.
4. The other assessment agents' leans are summed (weight x confidence x lean)
   and converted into sport units (goals/points of margin and total).
5. Final probability = consensus shifted by the model DELTA
   (logit p_final = logit p_consensus + clamp(logit f(theta_final) - logit f(theta_market))),
   which keeps model mis-fit from creating fake value.
6. Value (EV), price-deviation level, pricing-error flags and risk are
   evaluated per selection, and every result says where its data came from
   and how fresh it is.
"""
from __future__ import annotations

import logging
import math
import threading
from datetime import datetime
from typing import Optional

from app.agents.assessment import (FormAgent, HomeAwayAgent, InjuryAgent, LineupAgent, ProbabilityAgent,
                                   StatisticalAgent, SuspensionAgent)
from app.agents.base import AnalysisContext, AgentReport
from app.agents.market import OddsScannerAgent, RiskAgent, ValueDetectionAgent, main_market
from app.config import RISK_LEVELS
from app.core.markets import selection_label
from app.core.models import Absence, Fixture, LineupInfo, Quote, TeamStats, utcnow
from app.core.odds_math import clamp, kelly_fraction, logistic, logit, normalise
from app.core.sports import is_individual
from app.i18n import lang_of, tr
from app.services.registry import Registry
from app.services.repository import Repository
from app.services.scanner import compare_fixture
from app.sports import model_for

log = logging.getLogger("betfinder.analysis")

PHASE1 = [OddsScannerAgent()]
PHASE2 = [ProbabilityAgent(), FormAgent(), HomeAwayAgent(), InjuryAgent(), SuspensionAgent(), LineupAgent(), StatisticalAgent()]
PHASE3 = [ValueDetectionAgent(), RiskAgent()]
AGENT_ORDER = ["odds_scanner", "value_detection", "probability", "form", "home_away", "injuries",
               "suspensions", "lineups", "statistics", "risk"]
ASSESSMENT_KEYS = ["form", "home_away", "injuries", "suspensions", "lineups", "statistics"]


def _side(market: str, selection: str) -> int:
    if market in ("h2h", "h2h3", "dnb", "spreads"):
        return {"home": 1, "away": -1}.get(selection, 0)
    return 0


def _total_side(market: str, selection: str) -> int:
    if market == "totals":
        return 1 if selection == "over" else -1
    if market == "btts":
        return 1 if selection == "yes" else -1
    return 0


def stance(rep: AgentReport, market: str, selection: str) -> str:
    if rep.status in ("no_data", "error") or rep.confidence <= 0:
        return "no_data"
    if market in ("h2h", "h2h3") and selection == "draw":
        x = -abs(rep.lean_home)  # strong leans either way argue against a draw
    else:
        x = _side(market, selection) * rep.lean_home + _total_side(market, selection) * rep.lean_total
    if x > 0.1:
        return "supports"
    if x < -0.1:
        return "opposes"
    return "neutral"


def _source_kind(source: str) -> str:
    if source == "manual":
        return "manual"
    if source == "scraper":
        return "scraped"
    return "live"


class AnalysisService:
    def __init__(self, repo: Repository, registry: Registry):
        self.repo = repo
        self.registry = registry
        self._cache: dict[str, tuple[tuple, dict]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- inputs
    def _team_stats(self, fx: Fixture, manual_xg: dict) -> tuple[Optional[TeamStats], Optional[TeamStats]]:
        home = away = None
        for provider, teams in fx.team_refs.items():
            h = self.repo.team_stats(f"{provider}:{fx.competition_id}:{teams.get('home')}")
            a = self.repo.team_stats(f"{provider}:{fx.competition_id}:{teams.get('away')}")
            if h and a:
                home, away = h, a
                break
        # manually entered xG overrides / supplements provider data
        out = []
        for side, stats, team in (("home", home, fx.home), ("away", away, fx.away)):
            xg = (manual_xg or {}).get(side) or {}
            if xg.get("for") is not None and xg.get("against") is not None:
                stats = stats.model_copy() if stats else TeamStats(team=team, source="manual")
                stats.xg_for, stats.xg_against = float(xg["for"]), float(xg["against"])
                stats.xg_games, stats.xg_source = max(int(xg.get("games") or 6), 3), "manual"
            out.append(stats)
        return out[0], out[1]

    def _team_news(self, fx: Fixture) -> tuple[list[Absence], Optional[str], dict[str, LineupInfo], dict, dict]:
        manual = self.repo.team_news(fx.id)
        prov = self.repo.provider_news(fx.id)
        absences = list(manual["absences"])
        source = None
        if prov.get("absences_source"):
            source = prov["absences_source"]
            manual_names = {(a.team, a.player.lower()) for a in absences}
            for item in prov.get("absences", []):
                try:
                    a = Absence.model_validate(item)
                except Exception:
                    continue
                if (a.team, a.player.lower()) not in manual_names:  # manual entry overrides provider
                    absences.append(a)
        lineups = {}
        for side in ("home", "away"):
            m = manual["lineups"][side]
            p = prov.get("lineups", {}).get(side)
            if p and m.status != "confirmed":
                try:
                    pl = LineupInfo.model_validate(p)
                    if m.rotated is not None:
                        pl.rotated = m.rotated
                    lineups[side] = pl
                    continue
                except Exception:
                    pass
            lineups[side] = m
        return absences, source, lineups, manual.get("xg", {}), prov

    # ------------------------------------------------------------- analysis
    def analyze(self, fx: Fixture, settings: dict, now: Optional[datetime] = None, include_history: bool = False) -> dict:
        now = now or utcnow()
        key = (self.repo.store.version, include_history, tuple(sorted((k, str(v)) for k, v in settings.items())),
               int(now.timestamp() // 300))
        with self._lock:
            hit = self._cache.get(fx.id)
            if hit and hit[0] == key:
                return hit[1]
        result = self._analyze(fx, settings, now, include_history)
        with self._lock:
            if len(self._cache) > 3000:
                self._cache.clear()
            self._cache[fx.id] = (key, result)
        return result

    def _analyze(self, fx: Fixture, settings: dict, now: datetime, include_history: bool) -> dict:
        lang = lang_of(settings)
        competition = self.registry.competition_or_custom(fx.competition_id, fx.sport, fx.competition_name)
        model = model_for(fx.sport, competition.get("params"))
        quotes = self.repo.quotes(fx.id)
        comps = compare_fixture(fx, quotes, self.registry, settings, now, include_history)

        market_probs = {mid: c["consensus"]["probs"] for mid, c in comps.items() if c["consensus"]["probs"]}
        fit = model.fit_market(market_probs) if market_probs else None
        theta_m, rms = (fit if fit else (None, None))

        absences, absences_source, lineups, manual_xg, prov_news = self._team_news(fx)
        home_stats, away_stats = self._team_stats(fx, manual_xg)
        h2h = self.repo.h2h(fx.id)
        ctx = AnalysisContext(
            fixture=fx, competition=competition, model=model, comparisons=comps, market_params=theta_m,
            market_fit_rms=rms, home_stats=home_stats, away_stats=away_stats, h2h=h2h,
            absences=absences, absences_source=absences_source, lineups=lineups, settings=settings, now=now,
        )
        reports: dict[str, AgentReport] = {}
        for agent in PHASE1 + PHASE2:
            reports[agent.key] = agent.safe_run(ctx)
        ctx.reports = reports

        # ---- combine ---------------------------------------------------
        prob_rep = reports["probability"]
        theta_model = prob_rep.details.get("params") if prob_rep.status == "ok" else None
        w_model = float(settings["model_weight"]) * prob_rep.confidence
        if theta_m and theta_model:
            theta_b = model.blend(theta_m, theta_model, w_model)
        else:
            theta_b = theta_m or theta_model
        s_home = sum(a.weight * reports[a.key].confidence * reports[a.key].lean_home for a in PHASE2)
        s_total = sum(a.weight * reports[a.key].confidence * reports[a.key].lean_total for a in PHASE2)
        theta_f = model.shift(theta_b, s_home * model.units_home, s_total * model.units_total) if theta_b else None
        cap = float(settings["max_logit_shift"])

        leaning = [(r.confidence, r.lean_home) for k, r in reports.items()
                   if k in ASSESSMENT_KEYS + ["probability"] and r.confidence > 0 and abs(r.lean_home) > 0.05]
        disagreement = None
        if len(leaning) >= 2:
            num = abs(sum(c * l for c, l in leaning))
            den = sum(c * abs(l) for c, l in leaning)
            disagreement = round(1 - num / den, 3) if den else 0.0
        ctx.disagreement = disagreement

        final: dict[str, dict[str, float]] = {}
        prob_source: dict[str, str] = {}
        for mid, comp in comps.items():
            pm = comp["consensus"]["probs"]
            if pm and theta_m and theta_f:
                f_m, f_f = model.probabilities_for(theta_m, mid), model.probabilities_for(theta_f, mid)
                if f_m and f_f and all(s in f_m and s in f_f for s in pm):
                    adj = {s: logistic(logit(pm[s]) + clamp(logit(f_f[s]) - logit(f_m[s]), -cap, cap)) for s in pm}
                    final[mid] = normalise(adj)
                    moved = max(abs(final[mid][s] - pm[s]) for s in pm)
                    prob_source[mid] = "market+analysis" if moved > 0.002 else "market"
                    continue
            if pm:
                final[mid], prob_source[mid] = pm, "market"
            elif theta_f:
                p = model.probabilities_for(theta_f, mid)
                if p:
                    final[mid], prob_source[mid] = p, "model-only"

        match_conf, conf_parts = self._match_confidence(comps, reports, disagreement, is_individual(fx.sport))
        selections = self._selections(fx, comps, final, prob_source, settings, match_conf, lang)
        ctx.selections = selections
        for agent in PHASE3:
            reports[agent.key] = agent.safe_run(ctx)
        self._qualify(selections, settings, lang)
        for s in selections:
            s["agent_stances"] = {k: stance(reports[k], s["market"], s["selection"]) for k in ASSESSMENT_KEYS + ["probability"]}
            s["explanation"] = self._explain(s, lang)

        value_sels = sorted([s for s in selections if s["ev_pct"] is not None], key=lambda s: -s["ev_pct"])
        headline = next((s for s in value_sels if s["qualifies"]), value_sels[0] if value_sels else None)
        warnings = []
        for k in AGENT_ORDER:
            warnings.extend(reports[k].warnings)
        data_status = self._data_status(fx, quotes, home_stats, away_stats, absences, absences_source, lineups, h2h, prov_news)
        odds_times = [q.fetched_at for q in quotes]
        return {
            "fixture": self._fixture_dict(fx),
            "competition": {"id": competition["id"], "name": competition["name"], "country": competition.get("country", "")},
            "generated_at": now.isoformat(),
            "odds_updated_at": max(odds_times).isoformat() if odds_times else None,
            "data_status": data_status,
            "markets": list(comps.values()),
            "selections": selections,
            "headline": headline,
            "agents": [reports[k].to_dict() for k in AGENT_ORDER],
            "confidence": match_conf,
            "confidence_parts": conf_parts,
            "disagreement": disagreement,
            "model": {
                "market_params": theta_m, "model_params": theta_model, "final_params": theta_f,
                "fit_rms": round(rms, 4) if rms is not None else None,
                "model_weight": round(w_model, 3), "shift_home": round(s_home, 3), "shift_total": round(s_total, 3),
                "describe": model.describe(theta_f) if theta_f else None,
            },
            "flags": sorted({f for s in selections for f in s.get("pricing_flags", [])}),
            "warnings": list(dict.fromkeys(warnings)),
        }

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _fixture_dict(fx: Fixture) -> dict:
        return {
            "id": fx.id, "sport": fx.sport, "competition_id": fx.competition_id, "competition": fx.competition_name,
            "home": fx.home, "away": fx.away, "kickoff": fx.kickoff.isoformat(), "status": fx.status,
            "score_home": fx.score_home, "score_away": fx.score_away, "manual": fx.manual,
            "sources": sorted(fx.refs),
        }

    @staticmethod
    def _data_status(fx, quotes: list[Quote], hs, as_, absences, absences_source, lineups, h2h, prov_news) -> list[dict]:
        """Where every kind of input came from and when it was last updated."""
        items = []
        kinds: dict[str, dict] = {}
        for q in quotes:
            k = _source_kind(q.source)
            entry = kinds.setdefault(k, {"updated_at": q.fetched_at, "sources": set()})
            entry["sources"].add(q.source)
            entry["updated_at"] = max(entry["updated_at"], q.fetched_at)
        for k in ("live", "scraped", "manual"):
            if k in kinds:
                items.append({"kind": "odds", "status": k, "sources": sorted(kinds[k]["sources"]),
                              "updated_at": kinds[k]["updated_at"].isoformat()})
        if not kinds:
            items.append({"kind": "odds", "status": "missing", "sources": [], "updated_at": None})
        stats = [s for s in (hs, as_) if s]
        if stats:
            src = sorted({s.source for s in stats} | {s.xg_source for s in stats if s.xg_source})
            status = "manual" if src == ["manual"] else "historical"
            items.append({"kind": "statistics", "status": status, "sources": src,
                          "updated_at": max(s.fetched_at for s in stats).isoformat()})
        else:
            items.append({"kind": "statistics", "status": "missing", "sources": [], "updated_at": None})
        manual_abs = [a for a in absences if a.source == "manual"]
        if absences_source:
            items.append({"kind": "injuries", "status": "live", "sources": [absences_source],
                          "updated_at": prov_news.get("absences_at")})
        if manual_abs:
            items.append({"kind": "injuries", "status": "manual", "sources": ["manual"],
                          "updated_at": max(a.added_at for a in manual_abs).isoformat()})
        if not absences_source and not manual_abs:
            items.append({"kind": "injuries", "status": "missing", "sources": [], "updated_at": None})
        statuses = [lineups[s].status for s in ("home", "away")]
        lu_status = "confirmed" if statuses == ["confirmed", "confirmed"] else (
            "unconfirmed" if any(s != "unknown" for s in statuses) else "missing")
        lu_src = sorted({lineups[s].source for s in ("home", "away") if lineups[s].status != "unknown"})
        lu_time = [lineups[s].updated_at for s in ("home", "away") if lineups[s].status != "unknown"]
        items.append({"kind": "lineups", "status": lu_status, "sources": lu_src,
                      "updated_at": max(lu_time).isoformat() if lu_time else None})
        items.append({"kind": "h2h", "status": "historical" if h2h else "missing", "sources": ["provider"] if h2h else [],
                      "updated_at": None})
        return items

    @staticmethod
    def _match_confidence(comps: dict, reports: dict[str, AgentReport], disagreement: Optional[float],
                          individual: bool = False) -> tuple[int, dict]:
        main = main_market(comps)
        n_books = comps[main]["consensus"]["n_books"] if main else 0
        sharp = bool(main and comps[main]["consensus"]["sharp_books"])
        market = min(n_books, 8) / 8 * 25 + (15 if sharp else 0)
        weights = {"probability": 0.35, "form": 0.20, "home_away": 0.15, "injuries": 0.35, "suspensions": 0.25,
                   "lineups": 0.30, "statistics": 0.20}
        data = sum(w * reports[k].confidence / 0.75 for k, w in weights.items()) / sum(weights.values()) * 35
        data = min(35.0, data)
        if individual:
            # Combat/racket sports have no team-statistics agents; the estimate is market-based,
            # so data reliability mirrors market quality (plus any news entered).
            data = max(data, market / 40 * 30)
        else:
            # Without a statistics source the estimate is the margin-free consensus of many
            # bookmakers: a deep market with sharp books is itself solid evidence.  Real team
            # data can still add up to the full 35 points.
            data = max(data, market / 40 * 20)
        agreement = 7.5 if disagreement is None else (1 - disagreement) * 15
        freshness = 0.0
        if main:
            ages = [b["age_min"] for b in comps[main]["books"]]
            youngest = min(ages) if ages else 1e9
            freshness = 10 if youngest < 30 else 7 if youngest < 180 else 4 if youngest < 720 else 1
        parts = {"market": round(market, 1), "data": round(data, 1), "agreement": round(agreement, 1), "freshness": freshness}
        return int(round(min(100.0, market + data + agreement + freshness))), parts

    def _selections(self, fx: Fixture, comps: dict, final: dict, prob_source: dict, settings: dict,
                    match_conf: int, lang: str) -> list[dict]:
        my_books = set(settings.get("my_bookmakers") or [])
        stale_after = float(settings["stale_minutes"])
        warn_dev, alert_dev = float(settings["deviation_warning_pct"]), float(settings["deviation_alert_pct"])
        bankroll = float(settings["bankroll"])
        out = []
        for mid, comp in comps.items():
            probs = final.get(mid)
            consensus = comp["consensus"]["probs"]
            for s in comp["selections"]:
                views = comp["quotes"].get(s, [])
                if not views:
                    continue
                usable = [v for v in views if v["age_min"] <= stale_after and (not my_books or v["bookmaker"] in my_books)]
                best = usable[0] if usable else None
                overall = views[0]
                p = probs.get(s) if probs else None
                pick = best or overall
                price = pick.get("net_odds") or pick["odds"]  # exchange prices count after commission
                ev = (p * price - 1) * 100 if p is not None else None
                market_ev = (consensus[s] * price - 1) * 100 if consensus else None
                conf = match_conf
                n_books = comp["consensus"]["n_books"]
                if n_books < 3:
                    conf -= 12
                if prob_source.get(mid) == "model-only":
                    conf = min(conf, 40)
                if "possible_error" in pick["flags"]:
                    conf -= 8
                if best is None:
                    conf -= 20
                conf = int(clamp(conf, 0, 100))
                kelly_pct = kelly_fraction(p, price) * float(settings["kelly_fraction"]) * 100 if p else 0.0
                if ev is None or prob_source.get(mid) == "model-only" or n_books <= 1 or conf < 40:
                    value_status = "uncertain"
                else:
                    value_status = "positive" if ev > 0 else "negative"
                deviation = None
                if market_ev is not None:
                    deviation = "alert" if market_ev >= alert_dev else "warning" if market_ev >= warn_dev else "normal"
                out.append({
                    "fixture_id": fx.id,
                    "market_id": mid, "market": comp["market"], "line": comp["line"], "market_label": comp["label"],
                    "selection": s,
                    "label": selection_label(fx.sport, comp["market"], comp["line"], s, fx.home, fx.away, lang),
                    "best": {k: pick.get(k) for k in ("bookmaker", "name", "odds", "net_odds", "commission_pct", "source",
                                                      "flags", "age_min")},
                    "best_is_allowed": best is not None,
                    "best_overall": {k: overall[k] for k in ("bookmaker", "name", "odds")},
                    "n_quotes": len(views),
                    "n_books_market": n_books,
                    "implied_pct": round(100 / pick["odds"], 2),
                    "market_prob": round(consensus[s], 4) if consensus else None,
                    "final_prob": round(p, 4) if p is not None else None,
                    "fair_odds": round(1 / p, 3) if p else None,
                    "ev_pct": round(ev, 2) if ev is not None else None,
                    "market_ev_pct": round(market_ev, 2) if market_ev is not None else None,
                    # the same price against the fair price of the OTHER ordinary bookmakers (None = too few to tell)
                    "soft_ev_pct": pick.get("edge_vs_others_pct"),
                    # how good the reference price is: strong | weak | None (no sharp bookmaker or liquid exchange)
                    "anchor": comp["consensus"].get("anchor"),
                    "deviation_level": deviation,
                    "value_status": value_status,
                    "kelly_pct": round(kelly_pct, 2),
                    "stake_amount": round(bankroll * kelly_pct / 100, 2),
                    "currency": settings.get("currency", "DKK"),
                    "prob_source": prob_source.get(mid, "none"),
                    "confidence": conf,
                    "arbitrage_pct": comp["arbitrage_pct"],
                    "dev_vs_median_pct": pick.get("dev_vs_median_pct"),
                })
        return out

    @staticmethod
    def _qualify(selections: list[dict], settings: dict, lang: str) -> None:
        max_risk = RISK_LEVELS.index(settings["max_risk"])
        # A small edge is only trusted against a strong reference price (a sharp bookmaker or a
        # liquid exchange); every other market needs the larger one.
        base_ev = float(settings["min_ev_pct"])
        weak_ev = max(base_ev, float(settings.get("min_ev_weak_anchor_pct", base_ev)))
        soft_min = float(settings.get("min_soft_edge_pct", -100.0))
        for s in selections:
            reasons: list[tuple[str, str]] = []
            required = base_ev if s.get("anchor") == "strong" else weak_ev
            s["required_ev_pct"] = required
            # the lowest price at which this selection still has the required edge
            s["min_odds"] = None
            if s.get("final_prob"):
                need = (1 + required / 100) / s["final_prob"]
                keep = 1 - float(s["best"].get("commission_pct") or 0.0) / 100  # exchanges: before commission
                s["min_odds"] = math.ceil((1 + (need - 1) / keep) * 100 - 1e-9) / 100
            if s["ev_pct"] is None:
                reasons.append(("no_probability", tr(lang, "rj_no_prob")))
            elif s["ev_pct"] < required:
                reasons.append(("ev", tr(lang, "rj_ev", ev=f"{s['ev_pct']:+.1f}%", min=f"{required:g}")))
            min_edge = float(settings["min_market_edge_pct"])
            if s["market_ev_pct"] is not None and s["market_ev_pct"] < min_edge and min_edge > -100:
                reasons.append(("edge", tr(lang, "rj_edge", e=f"{s['market_ev_pct']:+.1f}%", min=f"{min_edge:+.1f}%")))
            # Safety net: value that rests on the reference price alone, with the price far below what
            # the other bookmakers consider fair, usually means the reference is the one that is wrong.
            if (soft_min > -100 and (s["ev_pct"] or 0) > 0 and s.get("soft_ev_pct") is not None
                    and s["soft_ev_pct"] < soft_min):
                reasons.append(("uncorroborated", tr(lang, "rj_uncorr", e=f"{s['soft_ev_pct']:+.1f}%", min=f"{soft_min:+.1f}%")))
            if s["confidence"] < int(settings["min_confidence"]):
                reasons.append(("confidence", tr(lang, "rj_conf", c=s["confidence"], min=settings["min_confidence"])))
            if s.get("risk_rating") and RISK_LEVELS.index(s["risk_rating"]) > max_risk:
                reasons.append(("risk", tr(lang, "rj_risk", r=tr(lang, f"risk_{s['risk_rating']}"),
                                           max=tr(lang, f"risk_{settings['max_risk']}"))))
            if not s["best_is_allowed"]:
                reasons.append(("bookmakers", tr(lang, "rj_books")))
            s["qualifies"] = not reasons
            s["rejections"] = [text for _, text in reasons]
            s["rejection_codes"] = [code for code, _ in reasons]

    @staticmethod
    def _explain(s: dict, lang: str) -> str:
        parts = []
        b = s["best"]
        if s["market_prob"] is not None:
            text = tr(lang, "ex_offers", book=b["name"], odds=f"{b['odds']:.2f}", fair=f"{1 / s['market_prob']:.2f}")
            if s.get("dev_vs_median_pct") is not None and s["dev_vs_median_pct"] > 0:
                text += tr(lang, "ex_above_median", pct=f"{s['dev_vs_median_pct']:.1f}")
            parts.append(text + ".")
        if b.get("commission_pct") and b.get("net_odds"):
            parts.append(tr(lang, "ex_commission", c=f"{b['commission_pct']:g}", net=f"{b['net_odds']:.2f}"))
        if s["final_prob"] is not None and s["market_prob"] is not None and abs(s["final_prob"] - s["market_prob"]) >= 0.005:
            parts.append(tr(lang, "ex_adjust", a=f"{s['market_prob']:.1%}", b=f"{s['final_prob']:.1%}"))
        elif s["prob_source"] == "model-only":
            parts.append(tr(lang, "ex_model_only"))
        sup = [tr(lang, f"short_{k}") for k, v in s.get("agent_stances", {}).items() if v == "supports"]
        opp = [tr(lang, f"short_{k}") for k, v in s.get("agent_stances", {}).items() if v == "opposes"]
        if sup:
            parts.append(tr(lang, "ex_support", names=", ".join(sup)))
        if opp:
            parts.append(tr(lang, "ex_against", names=", ".join(opp)))
        if "possible_pricing_error" in s.get("pricing_flags", []):
            parts.append(tr(lang, "ex_error"))
        if s.get("risk_factors"):
            parts.append(tr(lang, "ex_risk", factors="; ".join(s["risk_factors"][:3])))
        return " ".join(parts)
