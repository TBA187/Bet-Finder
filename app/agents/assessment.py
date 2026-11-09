"""Phase-2 agents: independent assessments of the match.

Each one looks at a different slice of evidence and returns a lean plus its
own confidence.  None of them sees another agent's output."""
from __future__ import annotations

from typing import Optional

from app.agents.base import (Agent, AnalysisContext, margin_scale, result_points, squash, total_scale)
from app.core.models import Absence, TeamStats
from app.core.sports import has_draw, is_individual, sport_label, unit


def _typical_home_edge(ctx: AnalysisContext) -> float:
    """League-typical home advantage in sport units (0 at neutral venues)."""
    p = ctx.model.params
    if p.get("neutral") or is_individual(ctx.sport):
        return 0.0
    if "home_goals" in p:
        return float(p["home_goals"]) - float(p["away_goals"])
    return float(p.get("home_adv", 0.0))


def _notes(ctx: AnalysisContext, notes: list) -> list[str]:
    out = []
    for n in notes:
        if isinstance(n, tuple):
            out.append(ctx.t(n[0], **n[1]))
        else:
            out.append(str(n))
    return out


# --------------------------------------------------------------------------- 3
class ProbabilityAgent(Agent):
    key = "probability"
    name = "Probability Agent"
    weight = 0.0  # contributes through the model blend, not through a lean

    def run(self, ctx: AnalysisContext):
        hs, as_ = ctx.home_stats, ctx.away_stats
        if is_individual(ctx.sport):
            return self.report(status="no_data", summary=ctx.t("prob_individual", sport=sport_label(ctx.sport, ctx.lang)))
        if not hs or not as_:
            return self.report(status="no_data", summary=ctx.t("prob_no_stats"), warnings=[ctx.t("prob_no_stats_warn")])
        res = ctx.model.from_stats(hs, as_)
        if res is None:
            return self.report(status="no_data", summary=ctx.t("prob_few_games"), data_sources=sorted({hs.source, as_.source}))
        theta, conf, notes = res
        model_probs = {}
        for mid in ctx.comparisons:
            p = ctx.model.probabilities_for(theta, mid)
            if p:
                model_probs[mid] = {k: round(v, 4) for k, v in p.items()}
        findings = _notes(ctx, notes)
        warnings = []
        max_gap = 0.0
        for mid in ("h2h", "dnb", "h2h3"):
            comp = ctx.comparisons.get(mid)
            if mid in model_probs and comp and comp["consensus"]["probs"]:
                market = comp["consensus"]["probs"]
                parts = []
                for s in comp["selections"]:
                    gap = model_probs[mid][s] - market[s]
                    max_gap = max(max_gap, abs(gap))
                    parts.append(ctx.t("prob_vs_market_part", label=comp["selection_labels"][s],
                                       model=f"{model_probs[mid][s]:.0%}", market=f"{market[s]:.0%}"))
                findings.append(ctx.t("prob_vs_market", parts=", ".join(parts)))
                break
        if max_gap > 0.12:
            conf *= 0.6
            warnings.append(ctx.t("prob_conflict"))
        lean_home = lean_total = 0.0
        if ctx.market_params:
            lean_home = squash((ctx.model.expected_margin(theta) - ctx.market_margin()) / margin_scale(ctx.sport), 1.5)
            lean_total = squash((ctx.model.expected_total(theta) - ctx.market_total()) / total_scale(ctx.sport), 1.5)
        d = ctx.model.describe(theta)
        sources = sorted({hs.source, as_.source} | ({hs.xg_source} if hs.xg_source else set()))
        return self.report(
            status="ok", confidence=conf, lean_home=lean_home, lean_total=lean_total,
            summary=ctx.t("prob_summary", sport=sport_label(ctx.sport, ctx.lang), home=d["expected_home"],
                          away=d["expected_away"], unit=unit(ctx.sport, ctx.lang)),
            findings=findings, warnings=warnings, data_sources=sources,
            details={"params": theta, "describe": d, "model_probs": model_probs, "max_gap_vs_market": round(max_gap, 4)},
        )


# --------------------------------------------------------------------------- 4
def _form(stats: TeamStats, sport: str, lang: str, n: int = 6) -> Optional[dict]:
    items = [r for r in stats.recent if r.get("scored") is not None and r.get("conceded") is not None][-n:]
    if len(items) < 3:
        return None
    weights = [0.8 ** (len(items) - 1 - i) for i in range(len(items))]
    wsum = sum(weights)
    pts = sum(w * result_points(sport, r["scored"], r["conceded"]) for w, r in zip(weights, items)) / wsum
    margin = sum(w * (r["scored"] - r["conceded"]) for w, r in zip(weights, items)) / wsum
    wdl = ("V", "U", "T") if lang == "da" else ("W", "D", "L")
    letters = "".join(wdl[0] if r["scored"] > r["conceded"] else wdl[1] if r["scored"] == r["conceded"] else wdl[2] for r in items)
    s, c = stats.overall("scored"), stats.overall("conceded")
    season_margin = (s - c) if s is not None and c is not None and stats.games >= 6 else None
    return {"n": len(items), "pts": pts, "margin": margin, "letters": letters, "season_margin": season_margin}


class FormAgent(Agent):
    key = "form"
    name = "Form Agent"
    weight = 0.20

    def run(self, ctx):
        if not ctx.home_stats or not ctx.away_stats:
            return self.report(status="no_data", summary=ctx.t("form_no_results"))
        fh, fa = _form(ctx.home_stats, ctx.sport, ctx.lang), _form(ctx.away_stats, ctx.sport, ctx.lang)
        if not fh or not fa:
            return self.report(status="no_data", summary=ctx.t("form_few"), data_sources=[ctx.home_stats.source])
        scale = margin_scale(ctx.sport)
        findings = [
            ctx.t("form_line", team=ctx.fixture.home, letters=fh["letters"], n=fh["n"], margin=f"{fh['margin']:+.1f}"),
            ctx.t("form_line", team=ctx.fixture.away, letters=fa["letters"], n=fa["n"], margin=f"{fa['margin']:+.1f}"),
        ]
        if fh["season_margin"] is not None and fa["season_margin"] is not None:
            mom_h = fh["margin"] - fh["season_margin"]
            mom_a = fa["margin"] - fa["season_margin"]
            lean = squash((mom_h - mom_a) / scale, 1.5)
            conf = 0.2 + 0.4 * min(fh["n"], fa["n"]) / 6
            findings.append(ctx.t("form_momentum", h=f"{mom_h:+.2f}", a=f"{mom_a:+.2f}"))
            summary = ctx.t("form_summary_momentum")
        else:
            lean = 0.6 * squash(((fh["margin"] - fa["margin"]) / scale) + (fh["pts"] - fa["pts"]), 1.5)
            conf = (0.2 + 0.4 * min(fh["n"], fa["n"]) / 6) * 0.6
            summary = ctx.t("form_summary_raw")
        if abs(lean) < 0.1:
            summary += ctx.t("form_level")
        return self.report(status="ok", confidence=conf, lean_home=lean, summary=summary, findings=findings,
                           data_sources=sorted({ctx.home_stats.source, ctx.away_stats.source}),
                           details={"home": fh, "away": fa})


# --------------------------------------------------------------------------- 5
class HomeAwayAgent(Agent):
    key = "home_away"
    name = "Home/Away Agent"
    weight = 0.15

    def run(self, ctx):
        if is_individual(ctx.sport):
            return self.report(status="no_data", summary=ctx.t("ha_not_relevant", sport=sport_label(ctx.sport, ctx.lang)))
        hs, as_ = ctx.home_stats, ctx.away_stats
        if not hs or not as_ or None in (hs.scored_home, as_.scored_away, hs.conceded_home, as_.conceded_away):
            return self.report(status="no_data", summary=ctx.t("ha_no_split"))
        if hs.games_home < 3 or as_.games_away < 3:
            return self.report(status="no_data", summary=ctx.t("ha_few"))
        neutral = bool(ctx.model.params.get("neutral"))
        hm, am = hs.scored_home - hs.conceded_home, as_.scored_away - as_.conceded_away
        ho, ao = hs.overall("scored") - hs.overall("conceded"), as_.overall("scored") - as_.overall("conceded")
        typical = _typical_home_edge(ctx) / 2
        excess = ((hm - ho) - typical) - ((am - ao) + typical)
        n = min(hs.games_home, as_.games_away)
        lean = squash(excess * n / (n + 6) / margin_scale(ctx.sport), 1.2)
        conf = 0.15 + 0.35 * min(n, 10) / 10

        def rec(w, d, l):
            if w is None:
                return "–"
            if has_draw(ctx.sport):
                return ctx.t("ha_record", w=w, d=d or 0, l=l or 0)
            return ctx.t("ha_record_nodraw", w=w, l=l or 0)

        findings = [
            ctx.t("ha_home_line", team=ctx.fixture.home, rec=rec(hs.wins_home, hs.draws_home, hs.losses_home),
                  f=f"{hs.scored_home:.1f}", a=f"{hs.conceded_home:.1f}"),
            ctx.t("ha_away_line", team=ctx.fixture.away, rec=rec(as_.wins_away, as_.draws_away, as_.losses_away),
                  f=f"{as_.scored_away:.1f}", a=f"{as_.conceded_away:.1f}"),
            ctx.t("ha_excess", x=f"{excess:+.2f}"),
        ]
        status, summary = "ok", ctx.t("ha_summary")
        if neutral:
            lean *= 0.3
            conf *= 0.5
            status, summary = "partial", ctx.t("ha_neutral")
        return self.report(status=status, confidence=conf, lean_home=lean, summary=summary, findings=findings,
                           data_sources=sorted({hs.source, as_.source}))


# ------------------------------------------------------------------------ 6/7
IMPORTANCE = {"key": 1.0, "regular": 0.5, "rotation": 0.15, "unknown": 0.35}
SPORT_FACTOR = {"football": 1.0, "basketball": 1.7, "handball": 1.25, "icehockey": 1.1, "americanfootball": 1.3,
                "baseball": 1.0, "mma": 2.5, "boxing": 2.5, "tennis": 2.5}


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
    noun_key = "noun_injuries"

    def run(self, ctx):
        relevant = [a for a in ctx.absences if a.kind in self.kinds]
        noun = ctx.t(self.noun_key)
        if ctx.absences_source is None and not ctx.absences:
            return self.report(status="no_data", summary=ctx.t("abs_no_source", noun=noun))
        ih, ia = _impact(relevant, "home", ctx.sport), _impact(relevant, "away", ctx.sport)
        findings = []
        for a in relevant:
            team = ctx.fixture.home if a.team == "home" else ctx.fixture.away
            findings.append(ctx.t("abs_line", team=team, player=a.player, kind=ctx.t(f"kind_{a.kind}"),
                                  reason=f", {a.reason}" if a.reason else "", importance=ctx.t(f"imp_{a.importance}")))
        sources = sorted({a.source for a in ctx.absences} | ({ctx.absences_source} if ctx.absences_source else set()))
        if not relevant:
            return self.report(status="ok", confidence=0.35, summary=ctx.t("abs_none", noun=noun), data_sources=sources)
        known = [a for a in relevant if a.importance != "unknown"]
        conf = 0.45 + 0.2 * len(known) / len(relevant)
        lean = squash(ia - ih, 1.6)
        if abs(lean) >= 0.1:
            summary = ctx.t("abs_found_favours", n=len(relevant), noun=noun, ih=f"{ih:.2f}", ia=f"{ia:.2f}",
                            side=ctx.t("side_home" if lean > 0 else "side_away"))
        else:
            summary = ctx.t("abs_found_balanced", n=len(relevant), noun=noun)
        warnings = [ctx.t("abs_unknown_importance")] if any(a.importance == "unknown" for a in relevant) else []
        return self.report(status="ok", confidence=conf, lean_home=lean, summary=summary, findings=findings,
                           warnings=warnings, data_sources=sorted({a.source for a in relevant}),
                           details={"impact_home": round(ih, 3), "impact_away": round(ia, 3)})


class InjuryAgent(_AbsenceAgent):
    key = "injuries"
    name = "Injury Agent"
    weight = 0.35
    kinds = ("injury", "doubtful", "other")
    noun_key = "noun_injuries"


class SuspensionAgent(_AbsenceAgent):
    key = "suspensions"
    name = "Suspension Agent"
    weight = 0.25
    kinds = ("suspension",)
    noun_key = "noun_suspensions"


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
            msg = ctx.t("lu_unknown")
            if ctx.sport == "football":
                msg += ctx.t("lu_unknown_football")
            warnings = [ctx.t("lu_close_warn")] if 0 < ctx.hours_to_kickoff < 1.0 and ctx.sport == "football" else []
            return self.report(status="no_data", summary=msg, warnings=warnings)
        x = 0.0
        findings = []
        status_word = {"confirmed": ctx.t("lu_confirmed"), "expected": ctx.t("lu_expected"), "unknown": ctx.t("lu_unknown_s")}
        for side, info in (("home", home), ("away", away)):
            if not info:
                continue
            team = ctx.fixture.home if side == "home" else ctx.fixture.away
            sign = -1 if side == "home" else 1
            findings.append(ctx.t("lu_status_line", team=team, status=status_word[info.status],
                                  formation=f" ({info.formation})" if info.formation else ""))
            if info.rotated:
                x += sign * 0.6
                findings.append(ctx.t("lu_rotate", team=team))
            if info.changes_from_last is not None:
                findings.append(ctx.t("lu_changes", team=team, n=info.changes_from_last))
                if info.changes_from_last >= 4:
                    x += sign * 0.1 * info.changes_from_last
        for a in ctx.absences:
            info = home if a.team == "home" else away
            if info and info.status == "confirmed" and any(a.player.lower() in p.lower() for p in info.players):
                findings.append(ctx.t("lu_absent_in_xi", player=a.player))
        confirmed = statuses.count("confirmed")
        conf = {2: 0.7, 1: 0.45}.get(confirmed, 0.3)
        lean = squash(x, 1.0)
        summary = ctx.t("lu_both" if confirmed == 2 else "lu_one" if confirmed == 1 else "lu_expected_only")
        if abs(lean) >= 0.1:
            summary += ctx.t("lu_favours", side=ctx.t("side_home" if lean > 0 else "side_away"))
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
        u = unit(ctx.sport, ctx.lang)
        have_stats = bool(hs and as_ and None not in (hs.overall("scored"), hs.overall("conceded"),
                                                      as_.overall("scored"), as_.overall("conceded")) and hs.games >= 4 and as_.games >= 4)
        mm = ctx.market_margin() if ctx.market_params else None
        mt = ctx.market_total() if ctx.market_params else None
        if have_stats:
            sh, ch, sa, ca = hs.overall("scored"), hs.overall("conceded"), as_.overall("scored"), as_.overall("conceded")
            stats_margin = ((sh - ch) - (sa - ca)) / 2 + _typical_home_edge(ctx)
            stats_total = (sh + ch + sa + ca) / 2
            findings.append(ctx.t("st_season", home=ctx.fixture.home, sh=f"{sh:.1f}", ch=f"{ch:.1f}",
                                  away=ctx.fixture.away, sa=f"{sa:.1f}", ca=f"{ca:.1f}", unit=u))
            findings.append(ctx.t("st_implied", m=f"{stats_margin:+.2f}", t=f"{stats_total:.1f}"))
            if ctx.sport == "football":
                for team, st in ((ctx.fixture.home, hs), (ctx.fixture.away, as_)):
                    if st.clean_sheets is not None and st.games:
                        findings.append(ctx.t("st_clean", team=team, cs=f"{st.clean_sheets / st.games:.0%}",
                                              fts=f"{(st.failed_to_score or 0) / st.games:.0%}"))
            if mm is not None:
                findings.append(ctx.t("st_market", m=f"{mm:+.2f}", t=f"{mt:.1f}"))
                lean_home = squash((stats_margin - mm) / margin_scale(ctx.sport), 1.5)
                lean_total = squash((stats_total - mt) / total_scale(ctx.sport), 1.5)
                conf = 0.2 + 0.35 * min(hs.games, as_.games, 15) / 15
            else:
                conf = 0.1
            sources |= {hs.source, as_.source}
        # xG screening (football): the xG difference is a better guide to true strength than goals
        if hs and as_ and hs.xg_for is not None and as_.xg_for is not None and hs.xg_against is not None and as_.xg_against is not None:
            xg_margin = ((hs.xg_for - hs.xg_against) - (as_.xg_for - as_.xg_against)) / 2 + _typical_home_edge(ctx)
            if mm is not None:
                findings.append(ctx.t("st_xg", m=f"{xg_margin:+.2f}", mm=f"{mm:+.2f}"))
                lean_home += 0.5 * squash((xg_margin - mm) / margin_scale(ctx.sport), 1.5)
                lean_total += 0.3 * squash(((hs.xg_for + hs.xg_against + as_.xg_for + as_.xg_against) / 2 - mt)
                                           / total_scale(ctx.sport), 1.5)
                conf += 0.1
            sources |= {hs.xg_source or "xg", as_.xg_source or "xg"}
        meetings = [m for m in (ctx.h2h or []) if m.get("score_home") is not None][:6]
        if len(meetings) >= 3:
            margins, totals = [], []
            for m in meetings:
                hh = m["score_home"] if m.get("home") == ctx.fixture.home else m["score_away"]
                aa = m["score_away"] if m.get("home") == ctx.fixture.home else m["score_home"]
                margins.append(hh - aa)
                totals.append(hh + aa)
            avg_m, avg_t = sum(margins) / len(margins), sum(totals) / len(totals)
            findings.append(ctx.t("st_h2h", n=len(meetings), m=f"{avg_m:+.1f}", team=ctx.fixture.home, t=f"{avg_t:.1f}"))
            if mm is not None and not is_individual(ctx.sport):
                lean_home += 0.25 * squash((avg_m - mm) / margin_scale(ctx.sport), 2.0)
                lean_total += 0.25 * squash((avg_t - mt) / total_scale(ctx.sport), 2.0)
                conf += 0.05
            sources.add("h2h")
        if not findings:
            return self.report(status="no_data", summary=ctx.t("st_none"))
        status = "ok" if (have_stats or len(sources) > 1) and mm is not None else "partial"
        summary = ctx.t("st_summary") if mm is not None else ctx.t("st_no_market")
        return self.report(status=status, confidence=min(conf, 0.7), lean_home=lean_home, lean_total=lean_total,
                           summary=summary, findings=findings, data_sources=sorted(sources))
