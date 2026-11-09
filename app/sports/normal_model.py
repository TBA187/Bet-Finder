"""Basketball and handball: discretised normal model for margin and total.

theta = {"mu": expected home margin, "total": expected total,
         "sd_m": margin standard deviation, "sd_t": total standard deviation}

The two sports deliberately use different parameters:
* Basketball: large scores, high variance (NBA margin sd ~13 points), the
  moneyline includes overtime so there is no draw; 3-way markets exist for
  regulation time.
* Handball: ~55-60 goals per match, margin sd ~5.5-6 goals, draws are a real
  outcome (~6-9%) and are priced in the main 1X2 market.
"""
from __future__ import annotations

import math

from app.core.markets import is_quarter_line, is_whole_line
from app.core.models import TeamStats
from app.core.odds_math import clamp, norm_cdf
from app.sports.base import SportModel, nelder_mead, shrink


class NormalScoreModel(SportModel):
    sport = "generic"
    has_draw_in_h2h = False

    def default_params(self) -> dict:  # pragma: no cover - overridden
        return {}

    # --- discrete normal helpers -------------------------------------------
    @staticmethod
    def _p_greater(mean: float, sd: float, k: float) -> float:
        """P(X > k) for an integer-valued X ~ N(mean, sd)."""
        if is_whole_line(k):
            return 1 - norm_cdf((k + 0.5 - mean) / sd)
        return 1 - norm_cdf((k - mean) / sd)

    @staticmethod
    def _p_less(mean: float, sd: float, k: float) -> float:
        if is_whole_line(k):
            return norm_cdf((k - 0.5 - mean) / sd)
        return norm_cdf((k - mean) / sd)

    def _three_way(self, theta):
        mu, sd = theta["mu"], theta["sd_m"]
        home = 1 - norm_cdf((0.5 - mu) / sd)
        away = norm_cdf((-0.5 - mu) / sd)
        return {"home": home, "draw": max(0.0, 1 - home - away), "away": away}

    def probabilities(self, theta, market, line):
        mu, sd = theta["mu"], theta["sd_m"]
        if market == "h2h":
            if self.has_draw_in_h2h:
                return self._three_way(theta)
            home = norm_cdf(mu / sd)  # overtime decides ties
            return {"home": home, "away": 1 - home}
        if market == "h2h3":
            return self._three_way(theta)
        if market == "dnb":
            p = self._three_way(theta)
            s = p["home"] + p["away"]
            return {"home": p["home"] / s, "away": p["away"] / s}
        if line is None or is_quarter_line(line):
            return None
        if market == "totals":
            over = self._p_greater(theta["total"], theta["sd_t"], line)
            under = self._p_less(theta["total"], theta["sd_t"], line)
            s = over + under
            return {"over": over / s, "under": under / s}
        if market == "spreads":
            # home covers when margin + line > 0  <=>  margin > -line
            k = -line
            home = self._p_greater(mu, sd, k)
            away = self._p_less(mu, sd, k)
            s = home + away
            return {"home": home / s, "away": away / s}
        return None

    def fit_market(self, market_probs):
        base = {"mu": 0.0, "total": float(self.params["avg_total"]),
                "sd_m": float(self.params["sd_m"]), "sd_t": float(self.params["sd_t"])}
        usable = {mid: p for mid, p in market_probs.items() if self.probabilities_for(base, mid)}
        if not usable:
            return None
        side = {m: p for m, p in usable.items() if not m.startswith("totals")}
        totals = {m: p for m, p in usable.items() if m.startswith("totals")}
        theta = dict(base)
        err_total, n_terms = 0.0, 0
        # Fitting the margin sd is only identifiable when a draw price exists.
        fit_sd = any(len(p) == 3 for p in side.values()) and self.params.get("fit_sd", True)
        if side:
            def loss_side(x):
                t = dict(theta, mu=x[0], sd_m=(clamp(math.exp(x[1]), *self.params["sd_m_bounds"]) if fit_sd else theta["sd_m"]))
                return sum(self._sq_error(self.probabilities_for(t, mid), p) for mid, p in side.items())
            x0 = [0.0, math.log(theta["sd_m"])] if fit_sd else [0.0]
            x, err = nelder_mead(loss_side, x0, step=1.0)
            theta["mu"] = round(x[0], 4)
            if fit_sd:
                theta["sd_m"] = round(clamp(math.exp(x[1]), *self.params["sd_m_bounds"]), 4)
            err_total += err
            n_terms += sum(len(p) for p in side.values())
        if totals:
            def loss_tot(x):
                t = dict(theta, total=x[0])
                return sum(self._sq_error(self.probabilities_for(t, mid), p) for mid, p in totals.items())
            first_line = float(next(iter(totals)).split("@")[1])
            x, err = nelder_mead(loss_tot, [first_line], step=2.0)
            theta["total"] = round(x[0], 3)
            err_total += err
            n_terms += sum(len(p) for p in totals.values())
        return theta, math.sqrt(err_total / max(n_terms, 1))

    def from_stats(self, home: TeamStats, away: TeamStats):
        avg_team = float(self.params["avg_total"]) / 2
        ha = 0.0 if self.params.get("neutral") else float(self.params["home_adv"])
        prior_home, prior_away = avg_team + ha / 2, avg_team - ha / 2
        notes: list[tuple] = []
        if home.scored_home is not None and away.scored_away is not None and home.games_home >= 2 and away.games_away >= 2:
            h_for = shrink(home.scored_home, home.games_home, prior_home, 5)
            h_against = shrink(home.conceded_home, home.games_home, prior_away, 5)
            a_for = shrink(away.scored_away, away.games_away, prior_away, 5)
            a_against = shrink(away.conceded_away, away.games_away, prior_home, 5)
            n = min(home.games_home, away.games_away)
            notes.append(("note_venue_scoring", {}))
        else:
            sh, ch, sa, ca = home.overall("scored"), home.overall("conceded"), away.overall("scored"), away.overall("conceded")
            if None in (sh, ch, sa, ca) or home.games < 3 or away.games < 3:
                return None
            h_for = shrink(sh, home.games, avg_team, 5) + ha / 4
            h_against = shrink(ch, home.games, avg_team, 5) - ha / 4
            a_for = shrink(sa, away.games, avg_team, 5) - ha / 4
            a_against = shrink(ca, away.games, avg_team, 5) + ha / 4
            n = min(home.games, away.games) // 2
            notes.append(("note_overall_scoring", {}))
        exp_home = (h_for + a_against) / 2
        exp_away = (a_for + h_against) / 2
        theta = {"mu": round(exp_home - exp_away, 3), "total": round(exp_home + exp_away, 3),
                 "sd_m": float(self.params["sd_m"]), "sd_t": float(self.params["sd_t"])}
        confidence = clamp(0.25 + 0.5 * min(n, 12) / 12, 0.0, 0.75)
        notes.append(("note_expected_score", {"home": f"{exp_home:.1f}", "away": f"{exp_away:.1f}", "n": n}))
        return theta, confidence, notes

    def blend(self, market, model, weight):
        w = clamp(weight, 0.0, 1.0)
        return {**market,
                "mu": (1 - w) * market["mu"] + w * model["mu"],
                "total": (1 - w) * market["total"] + w * model["total"]}

    def shift(self, theta, home_units, total_units):
        return {**theta, "mu": theta["mu"] + home_units, "total": max(1.0, theta["total"] + total_units)}

    def describe(self, theta):
        return {"kind": "score",
            "expected_home": round((theta["total"] + theta["mu"]) / 2, 1),
            "expected_away": round((theta["total"] - theta["mu"]) / 2, 1),
            "expected_total": round(theta["total"], 1),
            "expected_margin": round(theta["mu"], 1),
            "margin_sd": round(theta["sd_m"], 2),
        }

    def expected_margin(self, theta: dict) -> float:
        return theta["mu"]

    def expected_total(self, theta: dict) -> float:
        return theta["total"]


class BasketballModel(NormalScoreModel):
    sport = "basketball"
    has_draw_in_h2h = False
    units_home = 3.5
    units_total = 5.0

    def default_params(self) -> dict:
        # European defaults (EuroLeague-like). NBA overrides in the registry.
        return {"avg_total": 162.0, "home_adv": 3.0, "sd_m": 11.5, "sd_t": 15.5,
                "sd_m_bounds": (8.0, 16.0), "fit_sd": False, "neutral": False}


class HandballModel(NormalScoreModel):
    sport = "handball"
    has_draw_in_h2h = True
    units_home = 1.5
    units_total = 2.0

    def default_params(self) -> dict:
        return {"avg_total": 57.0, "home_adv": 1.8, "sd_m": 5.8, "sd_t": 6.2,
                "sd_m_bounds": (3.8, 9.0), "fit_sd": True, "neutral": False}


class AmericanFootballModel(NormalScoreModel):
    sport = "americanfootball"
    has_draw_in_h2h = False
    units_home = 3.0
    units_total = 4.0

    def default_params(self) -> dict:
        return {"avg_total": 45.0, "home_adv": 1.8, "sd_m": 13.5, "sd_t": 13.0,
                "sd_m_bounds": (9.0, 18.0), "fit_sd": False, "neutral": False}


class BaseballModel(NormalScoreModel):
    sport = "baseball"
    has_draw_in_h2h = False
    units_home = 0.5
    units_total = 0.8

    def default_params(self) -> dict:
        return {"avg_total": 8.8, "home_adv": 0.25, "sd_m": 4.3, "sd_t": 4.4,
                "sd_m_bounds": (3.0, 6.0), "fit_sd": False, "neutral": False}
