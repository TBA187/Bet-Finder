"""Pure odds / probability / value mathematics.

Everything in here is deterministic and side-effect free so it can be unit
tested in isolation.  All odds are decimal odds.

Key formulas (from the BetFinder spec):
    implied probability = 1 / decimal odds
    fair odds           = 1 / estimated probability
    EV %                = (estimated probability * decimal odds - 1) * 100

For markets with several outcomes the raw implied probabilities sum to more
than 1 (the bookmaker margin / overround).  ``devig`` removes that margin so
the probabilities sum to exactly 1.  Several methods are offered because the
margin is not spread evenly in practice (bookmakers load more margin on
long-shots: the favourite/long-shot bias).  Shin's method is the default.
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Iterable, Sequence

MIN_ODDS = 1.01
MAX_ODDS = 1000.0
DEVIG_METHODS = ("shin", "power", "multiplicative", "additive")

_STD_NORMAL = NormalDist()


class OddsError(ValueError):
    """Raised for odds values that cannot be real decimal odds."""


def validate_odds(odds: object) -> float:
    try:
        value = float(odds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise OddsError(f"Odds must be a number, got {odds!r}") from None
    if not math.isfinite(value) or value < MIN_ODDS or value > MAX_ODDS:
        raise OddsError(f"Decimal odds must be between {MIN_ODDS} and {MAX_ODDS}, got {value}")
    return value


def implied_probability(odds: float) -> float:
    """Raw implied probability of a single price (includes the margin)."""
    return 1.0 / validate_odds(odds)


def fair_odds(probability: float | None) -> float | None:
    if probability is None or probability <= 0:
        return None
    return 1.0 / probability


def expected_value(probability: float, odds: float) -> float:
    """Expected profit per unit staked (0.05 == +5%)."""
    return probability * odds - 1.0


def ev_percent(probability: float, odds: float) -> float:
    return expected_value(probability, odds) * 100.0


def overround(odds: Sequence[float]) -> float:
    """Sum of implied probabilities minus one (0.05 == 5% margin)."""
    return sum(1.0 / validate_odds(o) for o in odds) - 1.0


def margin_percent(odds: Sequence[float]) -> float:
    return overround(odds) * 100.0


def arbitrage_percent(best_odds: Sequence[float]) -> float | None:
    """Guaranteed return % when backing every outcome at the best prices.

    Returns None when there is no arbitrage (implied sum >= 1).
    """
    inv = sum(1.0 / validate_odds(o) for o in best_odds)
    if inv >= 1.0:
        return None
    return (1.0 / inv - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Margin removal
# ---------------------------------------------------------------------------

def devig(odds: Sequence[float], method: str = "shin") -> list[float]:
    """Return fair probabilities (summing to 1) for a complete market."""
    if len(odds) < 2:
        raise OddsError("A market needs at least two outcomes to remove the margin")
    implied = [1.0 / validate_odds(o) for o in odds]
    total = sum(implied)
    if method not in DEVIG_METHODS:
        raise ValueError(f"Unknown de-vig method {method!r}; use one of {DEVIG_METHODS}")
    # With no overround (or an arbitrage) the sophisticated methods have no
    # margin to model, so plain normalisation is the only sensible choice.
    if total <= 1.0 or method == "multiplicative":
        return [p / total for p in implied]
    if method == "additive":
        return _devig_additive(implied, total)
    if method == "power":
        return _devig_power(implied)
    return _devig_shin(implied, total)


def _devig_additive(implied: list[float], total: float) -> list[float]:
    n = len(implied)
    probs = [p - (total - 1.0) / n for p in implied]
    if min(probs) <= 0:
        return [p / total for p in implied]
    return probs


def _devig_power(implied: list[float]) -> list[float]:
    # Find k > 1 with sum(p_i ** k) == 1.
    lo, hi = 1.0, 2.0
    while sum(p ** hi for p in implied) > 1.0 and hi < 64:
        hi *= 2
    for _ in range(100):
        mid = (lo + hi) / 2
        if sum(p ** mid for p in implied) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    probs = [p ** k for p in implied]
    s = sum(probs)
    return [p / s for p in probs]


def _shin_probs(implied: list[float], total: float, z: float) -> list[float]:
    return [
        (math.sqrt(z * z + 4.0 * (1.0 - z) * p * p / total) - z) / (2.0 * (1.0 - z))
        for p in implied
    ]


def _devig_shin(implied: list[float], total: float) -> list[float]:
    lo, hi = 0.0, 0.5
    for _ in range(100):
        mid = (lo + hi) / 2
        if sum(_shin_probs(implied, total, mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    probs = _shin_probs(implied, total, (lo + hi) / 2)
    if min(probs) <= 0:
        return [p / total for p in implied]
    s = sum(probs)
    return [p / s for p in probs]


# ---------------------------------------------------------------------------
# Staking / combinations
# ---------------------------------------------------------------------------

def kelly_fraction(probability: float, odds: float) -> float:
    """Full-Kelly stake as a fraction of bankroll (0 when there is no edge)."""
    b = odds - 1.0
    if b <= 0:
        return 0.0
    f = (probability * odds - 1.0) / b
    return max(0.0, f)


def log_growth(probability: float, odds: float, fraction: float) -> float:
    """Expected log bankroll growth for staking ``fraction`` at ``odds``."""
    if fraction <= 0:
        return 0.0
    fraction = min(fraction, 0.999)
    return probability * math.log1p(fraction * (odds - 1.0)) + (1.0 - probability) * math.log1p(-fraction)


def combined_odds(odds: Iterable[float]) -> float:
    result = 1.0
    for o in odds:
        result *= o
    return result


def combined_probability(probs: Iterable[float]) -> float:
    """Joint probability assuming independent legs (different matches)."""
    result = 1.0
    for p in probs:
        result *= p
    return result


# ---------------------------------------------------------------------------
# Small numeric helpers used by the sport models
# ---------------------------------------------------------------------------

def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def logit(p: float) -> float:
    p = clamp(p, 1e-6, 1 - 1e-6)
    return math.log(p / (1.0 - p))


def logistic(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def norm_cdf(x: float) -> float:
    return _STD_NORMAL.cdf(x)


def normalise(probs: dict[str, float]) -> dict[str, float]:
    total = sum(probs.values())
    if total <= 0:
        return dict(probs)
    return {k: v / total for k, v in probs.items()}
