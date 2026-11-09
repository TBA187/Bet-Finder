"""Sport model interface + a tiny dependency-free Nelder-Mead optimiser."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Sequence

from app.core.markets import parse_market_id
from app.core.models import TeamStats


def nelder_mead(f: Callable[[list[float]], float], x0: Sequence[float], step: float = 0.15,
                max_iter: int = 400, tol: float = 1e-10) -> tuple[list[float], float]:
    n = len(x0)
    pts = [list(x0)]
    for i in range(n):
        p = list(x0)
        p[i] += step
        pts.append(p)
    vals = [f(p) for p in pts]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=vals.__getitem__)
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        centroid = [sum(p[j] for p in pts[:-1]) / n for j in range(n)]
        worst = pts[-1]
        xr = [c + (c - w) for c, w in zip(centroid, worst)]
        fr = f(xr)
        if fr < vals[0]:
            xe = [c + 2 * (c - w) for c, w in zip(centroid, worst)]
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = [c + 0.5 * (w - c) for c, w in zip(centroid, worst)]
            fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                best = pts[0]
                for i in range(1, n + 1):
                    pts[i] = [b + 0.5 * (p - b) for b, p in zip(best, pts[i])]
                    vals[i] = f(pts[i])
    i = min(range(n + 1), key=vals.__getitem__)
    return pts[i], vals[i]


def shrink(avg: Optional[float], n: int, prior: float, k: float = 6.0) -> float:
    """Bayesian-style shrinkage of a per-game average toward the league prior."""
    if avg is None or n <= 0:
        return prior
    return (avg * n + prior * k) / (n + k)


def xg_ok(stats: TeamStats) -> bool:
    return stats.xg_for is not None and stats.xg_against is not None and stats.xg_games >= 3


def xg_factor(goals_avg: Optional[float], xg_avg: Optional[float], xg_games: int) -> float:
    """Multiplier that moves a goals-based rating toward the xG-based one."""
    if not goals_avg or xg_avg is None or goals_avg <= 0:
        return 1.0
    w = 0.6 * min(1.0, xg_games / 6)
    blended = (1 - w) * goals_avg + w * xg_avg
    return max(0.5, min(1.8, blended / goals_avg))


class SportModel(ABC):
    """Each sport implements its own scoring model.

    Params are plain dicts so they can be shown in the UI and stored.
    ``units_home`` / ``units_total`` convert an agent "lean" of 1.0 into sport
    units (goals or points of expected margin / total).
    """

    sport: str
    units_home: float
    units_total: float

    def __init__(self, params: Optional[dict] = None):
        self.params = {**self.default_params(), **(params or {})}

    @abstractmethod
    def default_params(self) -> dict: ...

    @abstractmethod
    def probabilities(self, theta: dict, market: str, line: Optional[float]) -> Optional[dict[str, float]]:
        """Fair probabilities (conditional on no push) or None if unsupported."""

    @abstractmethod
    def fit_market(self, market_probs: dict[str, dict[str, float]]) -> Optional[tuple[dict, float]]:
        """Find parameters reproducing market probabilities. Returns (theta, rms error)."""

    @abstractmethod
    def from_stats(self, home: TeamStats, away: TeamStats) -> Optional[tuple[dict, float, list[str]]]:
        """Independent estimate from team statistics: (theta, confidence 0..1, notes)."""

    @abstractmethod
    def blend(self, market: dict, model: dict, weight: float) -> dict: ...

    @abstractmethod
    def shift(self, theta: dict, home_units: float, total_units: float) -> dict: ...

    @abstractmethod
    def describe(self, theta: dict) -> dict[str, Any]:
        """Human readable expected score etc."""

    def expected_margin(self, theta: dict) -> float:  # pragma: no cover - overridden
        return 0.0

    def expected_total(self, theta: dict) -> float:  # pragma: no cover - overridden
        return 0.0

    def probabilities_for(self, theta: dict, mid: str) -> Optional[dict[str, float]]:
        market, line = parse_market_id(mid)
        try:
            return self.probabilities(theta, market, line)
        except (ValueError, ZeroDivisionError, OverflowError):
            return None

    @staticmethod
    def _sq_error(model: Optional[dict[str, float]], market: dict[str, float]) -> float:
        if not model:
            return 0.0
        return sum((model.get(k, 0.0) - v) ** 2 for k, v in market.items())
