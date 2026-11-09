"""Two-competitor sports (MMA/UFC, boxing, tennis).

theta = {"s": strength difference in log-odds (first competitor minus
second), "d": draw probability (boxing only, 0 otherwise)}.

There is no score model, so totals/handicaps (rounds, games) are priced
from the bookmaker consensus only.  Agents can still shift ``s`` (for example
from manually entered news such as an injury or short-notice replacement).
"""
from __future__ import annotations

from typing import Optional

from app.core.models import TeamStats
from app.core.odds_math import clamp, logistic, logit
from app.sports.base import SportModel


class BinaryModel(SportModel):
    sport = "binary"
    units_home = 0.25  # log-odds per unit of lean
    units_total = 0.0

    def default_params(self) -> dict:
        return {"draw": 0.0, "neutral": True}

    def probabilities(self, theta: dict, market: str, line: Optional[float]) -> Optional[dict[str, float]]:
        p1 = logistic(theta["s"])
        if market == "h2h":
            return {"home": p1, "away": 1.0 - p1}
        if market == "h2h3":
            d = clamp(theta.get("d", 0.0), 0.0, 0.3)
            return {"home": (1 - d) * p1, "draw": d, "away": (1 - d) * (1 - p1)}
        return None

    def fit_market(self, market_probs):
        theta = {"s": 0.0, "d": 0.0}
        found = False
        if "h2h" in market_probs:
            p = market_probs["h2h"]
            theta["s"] = logit(p["home"] / (p["home"] + p["away"]))
            found = True
        if "h2h3" in market_probs:
            p = market_probs["h2h3"]
            theta["d"] = clamp(p.get("draw", 0.0), 0.0, 0.3)
            if not found:
                theta["s"] = logit(p["home"] / (p["home"] + p["away"]))
                found = True
        if not found:
            return None
        return {k: round(v, 5) for k, v in theta.items()}, 0.0

    def from_stats(self, home: TeamStats, away: TeamStats):
        return None  # no statistics feed for individual sports yet

    def blend(self, market, model, weight):
        w = clamp(weight, 0.0, 1.0)
        return {**market, "s": (1 - w) * market["s"] + w * model["s"]}

    def shift(self, theta, home_units, total_units):
        return {**theta, "s": theta["s"] + home_units}

    def describe(self, theta):
        p1 = logistic(theta["s"])
        return {"kind": "binary", "win_home": round(p1, 4), "win_away": round(1 - p1, 4),
                "draw": round(theta.get("d", 0.0), 4)}

    def expected_margin(self, theta: dict) -> float:
        return theta["s"]

    def expected_total(self, theta: dict) -> float:
        return 0.0
