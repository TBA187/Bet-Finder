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
6. Value (EV), pricing-error flags and risk are evaluated per selection.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

from app.agents.assessment import (FormAgent, HomeAwayAgent, InjuryAgent, LineupAgent, ProbabilityAgent,
                                   StatisticalAgent, SuspensionAgent)
from app.agents.base import AnalysisContext, AgentReport
from app.agents.market import OddsScannerAgent, RiskAgent, ValueDetectionAgent, main_market
from app.config import RISK_LEVELS
from app.core.markets import selection_label
from app.core.models import Absence, Fixture, LineupInfo, TeamStats, utcnow
from app.core.odds_math import clamp, kelly_fraction, logistic, logit, normalise
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


class AnalysisService:
    def __init__(self, repo: Repository, registry: Registry):
        self.repo = repo
        self.registry = registry
        self._cache: dict[str, tuple[tuple, dict]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- inputs
    def _team_stats(self, fx: Fixture) -> tuple[Optional[TeamStats], Optional[TeamStats]]:
        for provider, teams in fx.team_refs.items():
            home = self.repo.team_stats(f"{provider}:{fx.competition_id}:{teams.get('home')}")
            away = self.repo.team_stats(f"{provider}:{fx.competition_id}:{teams.get('away')}")
            if home and away:
                return home, away
        home = self.repo.team_stats(f"demo:{fx.competition_id}:{fx.home}")
        away = self.repo.team_stats(f"demo:{fx.competition_id}:{fx.away}")
        return home, away

    def _team_news(self, fx: Fixture) -> tuple[list[Absence], Optional[str], dict[str, LineupInfo]]:
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
        return absences, source, lineups

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
            if len(self._cache) > 2000:
                self._cache.clear()
            self._cache[fx.id] = (key, result)
        return result

    def _analyze(self, fx: Fixture, settings: dict, now: datetime, include_history: bool) -> dict:
        competition = self.registry.competition_or_custom(fx.competition_id, fx.sport, fx.competition_name)
        model = model_for(fx.sport, competition.get("params"))
        quotes = self.repo.quotes(fx.id)
        comps = compare_fixture(fx, quotes, self.registry, settings, now, include_history)

        market_probs = {mid: c["consensus"]["probs"] for mid, c in comps.items() if c["consensus"]["probs"]}
        fit = model.fit_market(market_probs) if market_probs else None
        theta_m, rms = (fit if fit else (None, None))

        home_stats, away_stats = self._team_stats(fx)
        absences, absences_source, lineups = self._team_news(fx)
        ctx = AnalysisContext(
            fixture=fx, competition=competition, model=model, comparisons=comps, market_params=theta_m,
            market_fit_rms=rms, home_stats=home_stats, away_stats=away_stats, h2h=self.repo.h2h(fx.id),
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

        match_conf, conf_parts = self._match_confidence(comps, reports, disagreement)
        selections = self._selections(fx, comps, final, prob_source, settings, match_conf)
        ctx.selections = selections
        for agent in PHASE3:
            reports[agent.key] = agent.safe_run(ctx)
        self._qualify(selections, settings)
        for s in selections:
            s["agent_stances"] = {k: stance(reports[k], s["market"], s["selection"]) for k in ASSESSMENT_KEYS + ["probability"]}
            s["explanation"] = self._explain(s, reports)

        value_sels = sorted([s for s in selections if s["ev_pct"] is not None], key=lambda s: -s["ev_pct"])
        headline = next((s for s in value_sels if s["qualifies"]), value_sels[0] if value_sels else None)
        warnings = []
        for k in AGENT_ORDER:
            warnings.extend(reports[k].warnings)
        return {
            "fixture": self._fixture_dict(fx),
            "competition": {"id": competition["id"], "name": competition["name"], "country": competition.get("country", "")},
            "generated_at": now.isoformat(),
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
            "score_home": fx.score_home, "score_away": fx.score_away, "is_demo": fx.is_demo, "manual": fx.manual,
            "sources": sorted(fx.refs),
        }

    @staticmethod
    def _match_confidence(comps: dict, reports: dict[str, AgentReport], disagreement: Optional[float]) -> tuple[int, dict]:
        main = main_market(comps)
        n_books = comps[main]["consensus"]["n_books"] if main else 0
        sharp = bool(main and comps[main]["consensus"]["sharp_books"])
        market = min(n_books, 8) / 8 * 25 + (15 if sharp else 0)
        weights = {"probability": 0.35, "form": 0.20, "home_away": 0.15, "injuries": 0.35, "suspensions": 0.25,
                   "lineups": 0.30, "statistics": 0.20}
        data = sum(w * reports[k].confidence / 0.75 for k, w in weights.items()) / sum(weights.values()) * 35
        data = min(35.0, data)
        agreement = 7.5 if disagreement is None else (1 - disagreement) * 15
        freshness = 0.0
        if main:
            ages = [b["age_min"] for b in comps[main]["books"]]
            youngest = min(ages) if ages else 1e9
            freshness = 10 if youngest < 30 else 7 if youngest < 180 else 4 if youngest < 720 else 1
        parts = {"market": round(market, 1), "data": round(data, 1), "agreement": round(agreement, 1), "freshness": freshness}
        return int(round(min(100.0, market + data + agreement + freshness))), parts

    def _selections(self, fx: Fixture, comps: dict, final: dict, prob_source: dict, settings: dict, match_conf: int) -> list[dict]:
        my_books = set(settings.get("my_bookmakers") or [])
        stale_after = float(settings["stale_minutes"])
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
                ev = (p * pick["odds"] - 1) * 100 if p is not None else None
                market_ev = (consensus[s] * pick["odds"] - 1) * 100 if consensus else None
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
                sel = {
                    "fixture_id": fx.id,
                    "market_id": mid, "market": comp["market"], "line": comp["line"], "market_label": comp["label"],
                    "selection": s,
                    "label": selection_label(fx.sport, comp["market"], comp["line"], s, fx.home, fx.away),
                    "best": {k: pick[k] for k in ("bookmaker", "name", "odds", "source", "flags", "age_min")},
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
                    "kelly_pct": round(kelly_fraction(p, pick["odds"]) * float(settings["kelly_fraction"]) * 100, 2) if p else 0.0,
                    "prob_source": prob_source.get(mid, "none"),
                    "confidence": int(clamp(conf, 0, 100)),
                    "arbitrage_pct": comp["arbitrage_pct"],
                    "dev_vs_median_pct": pick.get("dev_vs_median_pct"),
                }
                out.append(sel)
        return out

    @staticmethod
    def _qualify(selections: list[dict], settings: dict) -> None:
        max_risk = RISK_LEVELS.index(settings["max_risk"])
        for s in selections:
            reasons = []
            if s["ev_pct"] is None:
                reasons.append("no probability estimate")
            elif s["ev_pct"] < float(settings["min_ev_pct"]):
                reasons.append(f"EV {s['ev_pct']:+.1f}% below {settings['min_ev_pct']}%")
            min_edge = float(settings["min_market_edge_pct"])
            if s["market_ev_pct"] is not None and s["market_ev_pct"] < min_edge and min_edge > -100:
                reasons.append(f"price {s['market_ev_pct']:+.1f}% vs market fair price (needs {min_edge:+.1f}%)")
            if s["confidence"] < int(settings["min_confidence"]):
                reasons.append(f"confidence {s['confidence']} below {settings['min_confidence']}")
            if s.get("risk_rating") and RISK_LEVELS.index(s["risk_rating"]) > max_risk:
                reasons.append(f"risk {s['risk_rating'].replace('_', ' ')} above {settings['max_risk'].replace('_', ' ')}")
            if not s["best_is_allowed"]:
                reasons.append("no fresh price at your bookmakers")
            s["qualifies"] = not reasons
            s["rejections"] = reasons

    @staticmethod
    def _explain(s: dict, reports: dict[str, AgentReport]) -> str:
        parts = []
        b = s["best"]
        if s["market_prob"] is not None:
            parts.append(f"{b['name']} offers {b['odds']:.2f}. The margin-free market price is {1 / s['market_prob']:.2f}")
            if s.get("dev_vs_median_pct") is not None and s["dev_vs_median_pct"] > 0:
                parts[-1] += f", and this price is {s['dev_vs_median_pct']:.1f}% above the median bookmaker"
            parts[-1] += "."
        if s["final_prob"] is not None and s["market_prob"] is not None and abs(s["final_prob"] - s["market_prob"]) >= 0.005:
            parts.append(f"The agents adjust the probability from {s['market_prob']:.1%} to {s['final_prob']:.1%}.")
        elif s["prob_source"] == "model-only":
            parts.append("No bookmaker consensus exists, so the estimate comes from the statistical model only.")
        names = {r.agent: r.name.replace(" Agent", "") for r in reports.values()}
        sup = [names[k] for k, v in s.get("agent_stances", {}).items() if v == "supports"]
        opp = [names[k] for k, v in s.get("agent_stances", {}).items() if v == "opposes"]
        if sup:
            parts.append("Supported by: " + ", ".join(sup) + ".")
        if opp:
            parts.append("Against: " + ", ".join(opp) + ".")
        if "possible_pricing_error" in s.get("pricing_flags", []):
            parts.append("This looks like a possible pricing error. Check it before betting, because bookmakers may void obvious errors.")
        if s.get("risk_factors"):
            parts.append("Risk: " + "; ".join(s["risk_factors"][:3]) + ".")
        return " ".join(parts)
