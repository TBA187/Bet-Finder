"""Phase-2 agents: independent assessments of the match.

Each one looks at a different slice of evidence and returns a lean plus its
own confidence.  None of them sees another agent's output."""
from __future__ import annotations

from typing import Optional

from app.agents.base import (Agent, AnalysisContext, margin_scale, result_points, squash, total_scale)
from app.core.models import Absence, TeamStats


def _fmt(x: Optional[float], digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:+.{digits}f}"


def _typical_home_edge(ctx: AnalysisContext) -> float:
    """League-typical home advantage in sport units (0 at neutral venues)."""
    p = ctx.model.params
    if p.get("neutral"):
        return 0.0
    if ctx.sport == "football":
        return float(p["home_goals"]) - float(p["away_goals"])
    return float(p.get("home_adv", 0.0))


# --------------------------------------------------------------------------- 3
class ProbabilityAgent(Agent):
    key = "probability"
    name = "Probability Agent"
    weight = 0.0  # contributes through the model blend, not through a lean

    def run(self, ctx: AnalysisContext):
        hs, as_ = ctx.home_stats, ctx.away_stats
        if not hs or not as_:
            return self.report(status="no_data",
                               summary="No team statistics available, so no independent probability model. The estimate falls back to bookmaker consensus only.",
                               warnings=["Connect a statistics provider (API-Sports) for model-based probabilities."])
        res = ctx.model.from_stats(hs, as_)
        if res is None:
            return self.report(status="no_data", summary="Too few games played to build a reliable model.",
                               data_sources=sorted({hs.source, as_.source}))
        theta, conf, notes = res
        model_probs = {}
        for mid, comp in ctx.comparisons.items():
            p = ctx.model.probabilities_for(theta, mid)
            if p:
                model_probs[mid] = {k: round(v, 4) for k, v in p.items()}
        findings = list(notes)
        warnings = []
        max_gap = 0.0
        for mid in ("h2h", "dnb"):
            if mid in model_probs and ctx.comparisons.get(mid, {}).get("consensus", {}).get("probs"):
                market = ctx.comparisons[mid]["consensus"]["probs"]
                comp = ctx.comparisons[mid]
                parts = []
                for s in comp["selections"]:
                    gap = model_probs[mid][s] - market[s]
                    max_gap = max(max_gap, abs(gap))
                    parts.append(f"{comp['selection_labels'][s]} {model_probs[mid][s]:.0%} (market {market[s]:.0%})")
                findings.append("Model vs market: " + ", ".join(parts))
                break
        if max_gap > 0.12:
            conf *= 0.6
            warnings.append("The model disagrees strongly with the market. That is more often a data gap (injuries, transfers, rotation) than a bookmaker error, so the model's weight is reduced.")
        lean_home = lean_total = 0.0
        if ctx.market_params:
            lean_home = squash((ctx.model.expected_margin(theta) - ctx.market_margin()) / margin_scale(ctx.sport), 1.5)
            lean_total = squash((ctx.model.expected_total(theta) - ctx.market_total()) / total_scale(ctx.sport), 1.5)
        d = ctx.model.describe(theta)
        return self.report(
            status="ok", confidence=conf, lean_home=lean_home, lean_total=lean_total,
            summary=f"{ctx.sport.capitalize()} model expects {d['expected_home']} - {d['expected_away']} ({d['unit']}).",
            findings=findings, warnings=warnings, data_sources=sorted({hs.source, as_.source}),
            details={"params": theta, "describe": d, "model_probs": model_probs, "max_gap_vs_market": round(max_gap, 4)},
        )


# --------------------------------------------------------------------------- 4
def _form(stats: TeamStats, sport: str, n: int = 6) -> Optional[dict]:
    items = [r for r in stats.recent if r.get("scored") is not None and r.get("conceded") is not None][-n:]
    if len(items) < 3:
        return None
    weights = [0.8 ** (len(items) - 1 - i) for i in range(len(items))]
    wsum = sum(weights)
    pts = sum(w * result_points(sport, r["scored"], r["conceded"]) for w, r in zip(weights, items)) / wsum
    margin = sum(w * (r["scored"] - r["conceded"]) for w, r in zip(weights, items)) / wsum
    letters = "".join("W" if r["scored"] > r["conceded"] else "D" if r["scored"] == r["conceded"] else "L" for r in items)
    s, c = stats.overall("scored"), stats.overall("conceded")
    season_margin = (s - c) if s is not None and c is not None and stats.games >= 6 else None
    return {"n": len(items), "pts": pts, "margin": margin, "letters": letters, "season_margin": season_margin}


class FormAgent(Agent):
    key = "form"
    name = "Form Agent"
    weight = 0.20

    def run(self, ctx):
        if not ctx.home_stats or not ctx.away_stats:
            return self.report(status="no_data", summary="No recent results available for either team.")
        fh, fa = _form(ctx.home_stats, ctx.sport), _form(ctx.away_stats, ctx.sport)
        if not fh or not fa:
            return self.report(status="no_data", summary="Fewer than 3 recent results available for at least one team.",
                               data_sources=[ctx.home_stats.source])
        scale = margin_scale(ctx.sport)
        findings = [
            f"{ctx.fixture.home}: {fh['letters']} (last {fh['n']}, {fh['margin']:+.1f} per game)",
            f"{ctx.fixture.away}: {fa['letters']} (last {fa['n']}, {fa['margin']:+.1f} per game)",
        ]
        if fh["season_margin"] is not None and fa["season_margin"] is not None:
            mom_h = fh["margin"] - fh["season_margin"]
            mom_a = fa["margin"] - fa["season_margin"]
            lean = squash((mom_h - mom_a) / scale, 1.5)
            conf = 0.2 + 0.4 * min(fh["n"], fa["n"]) / 6
            findings.append(f"Momentum vs season average: home {mom_h:+.2f}, away {mom_a:+.2f}")
            summary = "Compares recent form with each team's season level, because the market already prices season strength."
        else:
            lean = 0.6 * squash(((fh["margin"] - fa["margin"]) / scale) + (fh["pts"] - fa["pts"]), 1.5)
            conf = (0.2 + 0.4 * min(fh["n"], fa["n"]) / 6) * 0.6
            summary = "Raw recent-form comparison. No season baseline is available, so confidence is reduced."
        if abs(lean) < 0.1:
            summary += " Form is broadly level."
        return self.report(status="ok", confidence=conf, lean_home=lean, summary=summary, findings=findings,
                           data_sources=sorted({ctx.home_stats.source, ctx.away_stats.source}),
                           details={"home": fh, "away": fa})


# --------------------------------------------------------------------------- 5
class HomeAwayAgent(Agent):
    key = "home_away"
    name = "Home/Away Agent"
    weight = 0.15

    def run(self, ctx):
        hs, as_ = ctx.home_stats, ctx.away_stats
        if not hs or not as_ or hs.scored_home is None or as_.scored_away is None or hs.conceded_home is None or as_.conceded_away is None:
            return self.report(status="no_data", summary="No home/away split statistics available.")
        if hs.games_home < 3 or as_.games_away < 3:
            return self.report(status="no_data", summary="Fewer than 3 home (or away) games played this season.")
        neutral = bool(ctx.model.params.get("neutral"))
        hm, am = hs.scored_home - hs.conceded_home, as_.scored_away - as_.conceded_away
        ho, ao = hs.overall("scored") - hs.overall("conceded"), as_.overall("scored") - as_.overall("conceded")
        typical = _typical_home_edge(ctx) / 2
        venue_h, venue_a = hm - ho, am - ao
        excess = (venue_h - typical) - (venue_a + typical)
        n = min(hs.games_home, as_.games_away)
        factor = n / (n + 6)
        lean = squash(excess * factor / margin_scale(ctx.sport), 1.2)
        conf = 0.15 + 0.35 * min(n, 10) / 10

        def rec(w, d, l):
            if w is None:
                return ""
            return f"{w}W {d or 0}D {l or 0}L" if ctx.sport != "basketball" else f"{w}W {l or 0}L"

        findings = [
            f"{ctx.fixture.home} at home: {rec(hs.wins_home, hs.draws_home, hs.losses_home)} avg {hs.scored_home:.1f}-{hs.conceded_home:.1f}",
            f"{ctx.fixture.away} away: {rec(as_.wins_away, as_.draws_away, as_.losses_away)} avg {as_.scored_away:.1f}-{as_.conceded_away:.1f}",
            f"Venue effect beyond a typical home advantage: {excess:+.2f}",
        ]
        status = "ok"
        summary = "Measures whether this home team is unusually strong at home, or this away team unusually weak away."
        if neutral:
            lean *= 0.3
            conf *= 0.5
            status = "partial"
            summary = "Neutral venue (tournament): home/away splits are only weakly relevant."
        return self.report(status=status, confidence=conf, lean_home=lean, summary=summary, findings=findings,
                           data_sources=sorted({hs.source, as_.source}))


# ------------------------------------------------------------------------ 6/7
IMPORTANCE = {"key": 1.0, "regular": 0.5, "rotation": 0.15, "unknown": 0.35}
SPORT_FACTOR = {"football": 1.0, "basketball": 1.7, "handball": 1.25}


def _impact(absences: list[Absence], side: str, sport: str) -> float:
    total = 0.0
    for a in absences:
        if a.team != side:
            continue
        w = IMPORTANCE.get(a.importance, 0.35)
        if a.kind == "doubtful":
            w *= 0.5
        total += w
    return total * SPORT_FACTOR.get(sport, 1.0)


class _AbsenceAgent(Agent):
    kinds: tuple[str, ...] = ()
    noun = "absences"

    def run(self, ctx):
        relevant = [a for a in ctx.absences if a.kind in self.kinds]
        if ctx.absences_source is None and not ctx.absences:
            return self.report(status="no_data",
                               summary=f"No {self.noun} data source for this match. Add team news manually, or connect API-Sports (football only).")
        ih, ia = _impact(relevant, "home", ctx.sport), _impact(relevant, "away", ctx.sport)
        findings = []
        for a in relevant:
            team = ctx.fixture.home if a.team == "home" else ctx.fixture.away
            findings.append(f"{team}: {a.player} ({a.kind}{', ' + a.reason if a.reason else ''}; importance {a.importance})")
        known = [a for a in relevant if a.importance != "unknown"]
        conf = 0.45 + (0.2 * len(known) / len(relevant) if relevant else 0.0)
        if not relevant:
            return self.report(status="ok", confidence=0.35,
                               summary=f"No {self.noun} reported for either team.",
                               data_sources=sorted({a.source for a in ctx.absences} | ({ctx.absences_source} if ctx.absences_source else set())))
        lean = squash(ia - ih, 1.6)
        side = "home" if lean > 0 else "away"
        summary = (f"{len(relevant)} {self.noun} found. Impact score home {ih:.2f} vs away {ia:.2f}, "
                   f"which favours the {side} side." if abs(lean) >= 0.1 else f"{len(relevant)} {self.noun} found. Impact is roughly balanced.")
        warnings = []
        if any(a.importance == "unknown" for a in relevant):
            warnings.append("Player importance is unknown for some absentees, so a default weight is used. Set importance under Team news for better accuracy.")
        return self.report(status="ok", confidence=conf, lean_home=lean, summary=summary, findings=findings,
                           warnings=warnings, data_sources=sorted({a.source for a in relevant}),
                           details={"impact_home": round(ih, 3), "impact_away": round(ia, 3)})


class InjuryAgent(_AbsenceAgent):
    key = "injuries"
    name = "Injury Agent"
    weight = 0.35
    kinds = ("injury", "doubtful", "other")
    noun = "injuries/doubts"


class SuspensionAgent(_AbsenceAgent):
    key = "suspensions"
    name = "Suspension Agent"
    weight = 0.25
    kinds = ("suspension",)
    noun = "suspensions"


# --------------------------------------------------------------------------- 8
class LineupAgent(Agent):
    key = "lineups"
    name = "Lineup Agent"
    weight = 0.30

    def run(self, ctx):
        home, away = ctx.lineups.get("home"), ctx.lineups.get("away")
        statuses = [x.status if x else "unknown" for x in (home, away)]
        flags = [x.rotated if x else None for x in (home, away)]
        changes = [x.changes_from_last if x else None for x in (home, away)]
        if statuses == ["unknown", "unknown"] and flags == [None, None]:
            msg = "Lineups are not confirmed yet."
            if ctx.sport == "football":
                msg += " They are usually published about an hour before kick-off."
            warnings = ["Kick-off is close and lineups are still unknown."] if 0 < ctx.hours_to_kickoff < 1.0 else []
            return self.report(status="no_data", summary=msg, warnings=warnings)
        x = 0.0
        findings = []
        for side, info in (("home", home), ("away", away)):
            team = ctx.fixture.home if side == "home" else ctx.fixture.away
            sign = -1 if side == "home" else 1
            if not info:
                continue
            findings.append(f"{team}: lineup {info.status}" + (f" ({info.formation})" if info.formation else ""))
            if info.rotated:
                x += sign * 0.6
                findings.append(f"{team} is expected to rotate or rest players")
            if info.changes_from_last is not None:
                findings.append(f"{team}: {info.changes_from_last} changes from the previous lineup")
                if info.changes_from_last >= 4:
                    x += sign * 0.1 * info.changes_from_last
        # absentees who are in a confirmed XI -> injury data is out of date
        for a in ctx.absences:
            info = home if a.team == "home" else away
            if info and info.status == "confirmed" and any(a.player.lower() in p.lower() for p in info.players):
                findings.append(f"{a.player} is listed as absent but appears in the confirmed lineup. Absence data may be outdated.")
        confirmed = statuses.count("confirmed")
        conf = {2: 0.7, 1: 0.45}.get(confirmed, 0.3)
        lean = squash(x, 1.0)
        summary = ("Both lineups confirmed." if confirmed == 2 else
                   "One lineup confirmed." if confirmed == 1 else "Expected lineups only (not confirmed).")
        if abs(lean) >= 0.1:
            summary += f" Lineup news favours the {'home' if lean > 0 else 'away'} side."
        sources = sorted({info.source for info in (home, away) if info and info.status != "unknown"})
        return self.report(status="ok", confidence=conf, lean_home=lean, summary=summary, findings=findings,
                           data_sources=sources, details={"statuses": statuses, "changes": changes})


# --------------------------------------------------------------------------- 9
class StatisticalAgent(Agent):
    key = "statistics"
    name = "Statistical Agent"
    weight = 0.20

    def run(self, ctx):
        hs, as_ = ctx.home_stats, ctx.away_stats
        findings: list[str] = []
        lean_home = lean_total = 0.0
        conf = 0.0
        sources = set()
        have_stats = bool(hs and as_ and None not in (hs.overall("scored"), hs.overall("conceded"),
                                                      as_.overall("scored"), as_.overall("conceded")) and hs.games >= 4 and as_.games >= 4)
        if have_stats:
            sh, ch, sa, ca = hs.overall("scored"), hs.overall("conceded"), as_.overall("scored"), as_.overall("conceded")
            stats_margin = ((sh - ch) - (sa - ca)) / 2 + _typical_home_edge(ctx)
            stats_total = (sh + ch + sa + ca) / 2
            unit = "pts" if ctx.sport == "basketball" else "goals"
            findings.append(f"Season averages: {ctx.fixture.home} {sh:.1f} for / {ch:.1f} against. {ctx.fixture.away} {sa:.1f} for / {ca:.1f} against ({unit}).")
            findings.append(f"Stats-implied margin {stats_margin:+.2f} and total {stats_total:.1f}")
            if ctx.sport == "football":
                for team, st in ((ctx.fixture.home, hs), (ctx.fixture.away, as_)):
                    if st.clean_sheets is not None and st.games:
                        findings.append(f"{team}: clean sheets {st.clean_sheets / st.games:.0%}, failed to score {(st.failed_to_score or 0) / st.games:.0%}")
            if ctx.market_params:
                mm, mt = ctx.market_margin(), ctx.market_total()
                findings.append(f"Market-implied margin {mm:+.2f} and total {mt:.1f}")
                lean_home = squash((stats_margin - mm) / margin_scale(ctx.sport), 1.5)
                lean_total = squash((stats_total - mt) / total_scale(ctx.sport), 1.5)
                conf = 0.2 + 0.35 * min(hs.games, as_.games, 15) / 15
            else:
                conf = 0.1
            sources |= {hs.source, as_.source}
        meetings = [m for m in (ctx.h2h or []) if m.get("score_home") is not None][:6]
        if len(meetings) >= 3:
            # margin from the perspective of the CURRENT home team
            margins, totals = [], []
            for m in meetings:
                hh = m["score_home"] if m.get("home") == ctx.fixture.home else m["score_away"]
                aa = m["score_away"] if m.get("home") == ctx.fixture.home else m["score_home"]
                margins.append(hh - aa)
                totals.append(hh + aa)
            avg_m, avg_t = sum(margins) / len(margins), sum(totals) / len(totals)
            findings.append(f"Head-to-head (last {len(meetings)}): avg margin {avg_m:+.1f} for {ctx.fixture.home}, avg total {avg_t:.1f}")
            if ctx.market_params:
                lean_home += 0.25 * squash((avg_m - ctx.market_margin()) / margin_scale(ctx.sport), 2.0)
                lean_total += 0.25 * squash((avg_t - ctx.market_total()) / total_scale(ctx.sport), 2.0)
                conf += 0.05
            sources.add("h2h")
        if not findings:
            return self.report(status="no_data", summary="No season statistics or head-to-head data available.")
        status = "ok" if have_stats and ctx.market_params else "partial"
        summary = "Compares stats-implied margin and total with what the bookmaker prices imply."
        if not ctx.market_params:
            summary = "Statistics available but no complete market to compare against."
        return self.report(status=status, confidence=conf, lean_home=lean_home, lean_total=lean_total,
                           summary=summary, findings=findings, data_sources=sorted(sources))
