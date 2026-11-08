"""Settle a selection against a final score.

Returns the payout multiplier per unit staked:
    win -> odds, loss -> 0, push/void -> 1,
    half win -> (1 + odds) / 2, half loss -> 0.5  (Asian quarter lines)
"""
from __future__ import annotations

from typing import Optional

from app.core.markets import is_quarter_line


def _line_outcome(value: float) -> str:
    if value > 1e-9:
        return "win"
    if value < -1e-9:
        return "lose"
    return "push"


def _single(market: str, line: Optional[float], selection: str, h: int, a: int) -> str:
    if market in ("h2h", "h2h3"):
        actual = "home" if h > a else "away" if a > h else "draw"
        return "win" if actual == selection else "lose"
    if market == "dnb":
        if h == a:
            return "push"
        actual = "home" if h > a else "away"
        return "win" if actual == selection else "lose"
    if market == "btts":
        both = h > 0 and a > 0
        return "win" if (selection == "yes") == both else "lose"
    if market == "totals":
        diff = (h + a) - line
        return _line_outcome(diff if selection == "over" else -diff)
    if market == "spreads":
        diff = (h - a) + line
        return _line_outcome(diff if selection == "home" else -diff)
    raise ValueError(f"Cannot settle market {market}")


def outcome(market: str, line: Optional[float], selection: str, h: int, a: int) -> str:
    """win | lose | push | half_win | half_lose"""
    if line is not None and is_quarter_line(line):
        parts = [_single(market, line - 0.25, selection, h, a), _single(market, line + 0.25, selection, h, a)]
        if parts[0] == parts[1]:
            return parts[0]
        if "win" in parts:
            return "half_win" if "push" in parts else "win"  # win+lose can't happen on adjacent lines
        return "half_lose"
    return _single(market, line, selection, h, a)


def payout(result: str, odds: float) -> float:
    return {"win": odds, "lose": 0.0, "push": 1.0, "void": 1.0,
            "half_win": (1 + odds) / 2, "half_lose": 0.5}[result]
