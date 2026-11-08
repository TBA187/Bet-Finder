"""Market vocabulary shared by providers, the scanner, models and the UI.

A market is identified by ``market_id`` = ``"<market>"`` or ``"<market>@<line>"``.

    h2h      match result. Football/handball: home/draw/away (regular time).
             Basketball: home/away moneyline including overtime.
    h2h3     basketball 3-way result in regulation time: home/draw/away.
    dnb      draw no bet: home/away (stake returned on a draw).
    totals   over/under ``line`` total goals/points.
    spreads  handicap. ``line`` is the HOME team's handicap (-1.5 means the
             home team must win by 2+). Selection "away" is the away team
             with the opposite handicap.
    btts     both teams to score (football): yes/no.
"""
from __future__ import annotations

import math

MARKET_TYPES = ("h2h", "h2h3", "dnb", "totals", "spreads", "btts")
LINE_MARKETS = ("totals", "spreads")

_SELECTIONS = {
    "h2h3": ("home", "draw", "away"),
    "dnb": ("home", "away"),
    "totals": ("over", "under"),
    "spreads": ("home", "away"),
    "btts": ("yes", "no"),
}

SPORTS = ("football", "basketball", "handball")


def selections_for(sport: str, market: str) -> tuple[str, ...]:
    if market == "h2h":
        return ("home", "away") if sport == "basketball" else ("home", "draw", "away")
    try:
        return _SELECTIONS[market]
    except KeyError:
        raise ValueError(f"Unknown market {market!r}") from None


def supported_markets(sport: str) -> tuple[str, ...]:
    if sport == "football":
        return ("h2h", "dnb", "totals", "spreads", "btts")
    if sport == "basketball":
        return ("h2h", "h2h3", "totals", "spreads")
    return ("h2h", "dnb", "totals", "spreads")


def normalise_line(line: float | str | None) -> float | None:
    if line is None or line == "":
        return None
    value = float(line)
    if not math.isfinite(value):
        raise ValueError("Line must be finite")
    # Lines are always multiples of a quarter.
    return round(value * 4) / 4


def market_id(market: str, line: float | None = None) -> str:
    if market in LINE_MARKETS:
        if line is None:
            raise ValueError(f"Market {market} needs a line")
        return f"{market}@{_fmt_line(line)}"
    return market


def parse_market_id(mid: str) -> tuple[str, float | None]:
    if "@" in mid:
        market, line = mid.split("@", 1)
        return market, float(line)
    return mid, None


def _fmt_line(line: float) -> str:
    text = f"{line:+.2f}" if line != 0 else "0"
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return text.lstrip("+") if not text.startswith("-") else text


def is_quarter_line(line: float | None) -> bool:
    return line is not None and abs(line * 2 - round(line * 2)) > 1e-9


def is_whole_line(line: float | None) -> bool:
    return line is not None and abs(line - round(line)) < 1e-9


def score_word(sport: str) -> str:
    return "points" if sport == "basketball" else "goals"


def market_label(sport: str, market: str, line: float | None = None) -> str:
    if market == "h2h":
        return "Moneyline (incl. OT)" if sport == "basketball" else "Match result 1X2"
    if market == "h2h3":
        return "3-way result (regulation)"
    if market == "dnb":
        return "Draw no bet"
    if market == "totals":
        return f"Total {score_word(sport)} {line:g}"
    if market == "spreads":
        return f"Handicap {_signed(line)} (home)"
    if market == "btts":
        return "Both teams to score"
    return market


def _signed(line: float | None) -> str:
    if line is None:
        return ""
    return f"{line:+g}" if line != 0 else "0"


def selection_label(sport: str, market: str, line: float | None, selection: str, home: str, away: str) -> str:
    if market in ("h2h", "h2h3", "dnb"):
        return {"home": home, "away": away, "draw": "Draw"}.get(selection, selection)
    if market == "totals":
        return f"{selection.capitalize()} {line:g}"
    if market == "spreads":
        if selection == "home":
            return f"{home} {_signed(line)}"
        return f"{away} {_signed(-line if line else 0.0)}"
    if market == "btts":
        return f"BTTS {selection.capitalize()}"
    return selection


def swap_sides(market: str, line: float | None, selection: str) -> tuple[float | None, str]:
    """Translate a selection when a provider lists home/away the other way round."""
    swap = {"home": "away", "away": "home"}
    if market == "spreads":
        return (-line if line is not None else None), swap.get(selection, selection)
    if market in ("h2h", "h2h3", "dnb"):
        return line, swap.get(selection, selection)
    return line, selection
