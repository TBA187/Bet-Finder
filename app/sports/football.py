"""Football: bivariate Poisson goals model with a Dixon-Coles low-score correction.

theta = {"lh": expected home goals, "la": expected away goals}
Markets are settled on regular time (90 minutes + stoppage time).
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional

from app.core.markets import is_quarter_line
from app.core.models import TeamStats
from app.core.odds_math import clamp
from app.sports.base import SportModel, nelder_mead, shrink


@lru_cache(maxsize=4096)
def _matrix(lh: float, la: float, rho: float, n: int) -> tuple[tuple[float, ...], ...]:
    ph = [math.exp(-lh) * lh ** i / math.factorial(i) for i in range(n + 1)]
    pa = [math.exp(-la) * la ** j / math.factorial(j) for j in range(n + 1)]
    m = [[ph[i] * pa[j] for j in range(n + 1)] for i in range(n + 1)]
    # Dixon-Coles adjustment: real football has more 0-0/1-1 and fewer 1-0/0-1
    # than independent Poisson predicts.
    m[0][0] *= max(0.0, 1 - lh * la * rho)
    m[0][1] *= max(0.0, 1 + lh * rho)
    m[1][0] *= max(0.0, 1 + la * rho)
    m[1][1] *= max(0.0, 1 - rho)
    total = sum(map(sum, m))
    return tuple(tuple(v / total for v in row) for row in m)


class FootballModel(SportModel):
    sport = "football"
    units_home = 0.30   # goals of supremacy per unit of lean
    units_total = 0.25  # goals of total per unit of lean

    def default_params(self) -> dict:
        return {"home_goals": 1.50, "away_goals": 1.20, "rho": -0.06, "max_goals": 10, "neutral": False}

    def _m(self, theta: dict):
        lh = round(clamp(theta["lh"], 0.05, 6.0), 4)
        la = round(clamp(theta["la"], 0.05, 6.0), 4)
        return _matrix(lh, la, float(self.params["rho"]), int(self.params["max_goals"]))

    def probabilities(self, theta: dict, market: str, line: Optional[float]) -> Optional[dict[str, float]]:
        m = self._m(theta)
        n = len(m)
        if market in ("h2h", "dnb"):
            home = sum(m[i][j] for i in range(n) for j in range(n) if i > j)
            draw = sum(m[i][i] for i in range(n))
            away = 1.0 - home - draw
            if market == "h2h":
                return {"home": home, "draw": draw, "away": away}
            return {"home": home / (home + away), "away": away / (home + away)}
        if market == "btts":
            yes = sum(m[i][j] for i in range(1, n) for j in range(1, n))
            return {"yes": yes, "no": 1.0 - yes}
        if line is None or is_quarter_line(line):
            return None  # quarter lines: priced from the market only
        if market == "totals":
            over = sum(m[i][j] for i in range(n) for j in range(n) if i + j > line + 1e-9)
            under = sum(m[i][j] for i in range(n) for j in range(n) if i + j < line - 1e-9)
        elif market == "spreads":
            over = sum(m[i][j] for i in range(n) for j in range(n) if i - j + line > 1e-9)
            under = sum(m[i][j] for i in range(n) for j in range(n) if i - j + line < -1e-9)
        else:
            return None
        s = over + under
        if s <= 0:
            return None
        a, b = ("over", "under") if market == "totals" else ("home", "away")
        return {a: over / s, b: under / s}

    def fit_market(self, market_probs):
        usable = {mid: p for mid, p in market_probs.items() if self.probabilities_for({"lh": 1.4, "la": 1.1}, mid)}
        if not usable:
            return None

        def loss(x):
            theta = {"lh": math.exp(x[0]), "la": math.exp(x[1])}
            return sum(self._sq_error(self.probabilities_for(theta, mid), p) for mid, p in usable.items())

        x, err = nelder_mead(loss, [math.log(self.params["home_goals"]), math.log(self.params["away_goals"])])
        theta = {"lh": round(math.exp(x[0]), 4), "la": round(math.exp(x[1]), 4)}
        n_terms = sum(len(p) for p in usable.values())
        return theta, math.sqrt(err / max(n_terms, 1))

    def from_stats(self, home: TeamStats, away: TeamStats):
        lg_h, lg_a = float(self.params["home_goals"]), float(self.params["away_goals"])
        if self.params.get("neutral"):
            lg_h = lg_a = (lg_h + lg_a) / 2
        notes: list[str] = []
        use_split = home.scored_home is not None and away.scored_away is not None and home.games_home >= 2 and away.games_away >= 2
        if use_split:
            att_h = shrink(home.scored_home, home.games_home, lg_h) / lg_h
            def_a = shrink(away.conceded_away, away.games_away, lg_h) / lg_h
            att_a = shrink(away.scored_away, away.games_away, lg_a) / lg_a
            def_h = shrink(home.conceded_home, home.games_home, lg_a) / lg_a
            n = min(home.games_home, away.games_away)
            notes.append("Venue-specific attack/defence ratings")
        else:
            sh, ch = home.overall("scored"), home.overall("conceded")
            sa, ca = away.overall("scored"), away.overall("conceded")
            if None in (sh, ch, sa, ca) or home.games < 3 or away.games < 3:
                return None
            avg = (lg_h + lg_a) / 2
            att_h = shrink(sh, home.games, avg) / avg
            def_h = shrink(ch, home.games, avg) / avg
            att_a = shrink(sa, away.games, avg) / avg
            def_a = shrink(ca, away.games, avg) / avg
            n = min(home.games, away.games) // 2
            notes.append("Overall ratings (no home/away split available)")
        lh = clamp(lg_h * att_h * def_a, 0.15, 4.5)
        la = clamp(lg_a * att_a * def_h, 0.15, 4.5)
        confidence = clamp(0.25 + 0.5 * min(n, 10) / 10, 0.0, 0.75)
        notes.append(f"Expected goals {lh:.2f} - {la:.2f} (sample {n} games per side)")
        return {"lh": round(lh, 4), "la": round(la, 4)}, confidence, notes

    def blend(self, market, model, weight):
        w = clamp(weight, 0.0, 1.0)
        return {k: math.exp((1 - w) * math.log(market[k]) + w * math.log(model[k])) for k in ("lh", "la")}

    def shift(self, theta, home_units, total_units):
        sup = theta["lh"] - theta["la"] + home_units
        tot = max(0.4, theta["lh"] + theta["la"] + total_units)
        lh, la = (tot + sup) / 2, (tot - sup) / 2
        return {"lh": clamp(lh, 0.05, 6.0), "la": clamp(la, 0.05, 6.0)}

    def describe(self, theta):
        return {
            "expected_home": round(theta["lh"], 2),
            "expected_away": round(theta["la"], 2),
            "expected_total": round(theta["lh"] + theta["la"], 2),
            "expected_margin": round(theta["lh"] - theta["la"], 2),
            "unit": "goals",
        }

    # Helpers used by agents --------------------------------------------------
    def expected_margin(self, theta: dict) -> float:
        return theta["lh"] - theta["la"]

    def expected_total(self, theta: dict) -> float:
        return theta["lh"] + theta["la"]
