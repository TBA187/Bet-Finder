"""Market vocabulary shared by providers, the scanner, models and the UI.

A market is identified by ``market_id`` = ``"<market>"`` or ``"<market>@<line>"``.

    h2h      main result market.  Includes a draw for sports where the
             regular-time result can be a draw (football, handball); a 2-way
             moneyline (incl. overtime / extra innings) for the others.
    h2h3     3-way result in regulation time (basketball, ice hockey) or a
             fight result including the draw (boxing).
    dnb      draw no bet: home/away (stake returned on a draw).
    totals   over/under ``line`` (goals, points, runs, rounds or games).
    spreads  handicap. ``line`` is the HOME/first competitor's handicap.
    btts     both teams to score (football): yes/no.

"home"/"away" mean the first/second listed competitor in individual sports.
"""
from __future__ import annotations

import math

from app.core.sports import SPORTS, has_draw, is_individual, unit

MARKET_TYPES = ("h2h", "h2h3", "dnb", "totals", "spreads", "btts")
LINE_MARKETS = ("totals", "spreads")

_SELECTIONS = {
    "h2h3": ("home", "draw", "away"),
    "dnb": ("home", "away"),
    "totals": ("over", "under"),
    "spreads": ("home", "away"),
    "btts": ("yes", "no"),
}


def selections_for(sport: str, market: str) -> tuple[str, ...]:
    if market == "h2h":
        return ("home", "draw", "away") if has_draw(sport) else ("home", "away")
    try:
        return _SELECTIONS[market]
    except KeyError:
        raise ValueError(f"Unknown market {market!r}") from None


def supported_markets(sport: str) -> tuple[str, ...]:
    return SPORTS.get(sport, {}).get("markets", ("h2h",))


def normalise_line(line: float | str | None) -> float | None:
    if line is None or line == "":
        return None
    value = float(line)
    if not math.isfinite(value):
        raise ValueError("Line must be finite")
    return round(value * 4) / 4  # lines are always multiples of a quarter


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


def score_word(sport: str, lang: str = "en") -> str:
    return unit(sport, lang)


_LABELS = {
    "h2h_draw": ("Match result 1X2", "Kampresultat 1X2"),
    "h2h_ot": ("Moneyline (incl. overtime)", "Vinder (inkl. forlænget spilletid)"),
    "h2h_fight": ("Winner", "Vinder"),
    "h2h3": ("3-way result (regulation)", "3-vejs resultat (ordinær tid)"),
    "h2h3_fight": ("Fight result incl. draw", "Kampresultat inkl. uafgjort"),
    "dnb": ("Draw no bet", "Uafgjort = retur"),
    "totals": ("Total {unit} {line}", "Total {unit} {line}"),
    "spreads": ("Handicap {line} (home)", "Handicap {line} (hjemme)"),
    "spreads_ind": ("Handicap {line} ({unit})", "Handicap {line} ({unit})"),
    "btts": ("Both teams to score", "Begge hold scorer"),
}


def _l(key: str, lang: str, **kw) -> str:
    en, da = _LABELS[key]
    return (da if lang == "da" else en).format(**kw)


def market_label(sport: str, market: str, line: float | None = None, lang: str = "en") -> str:
    if market == "h2h":
        if has_draw(sport):
            return _l("h2h_draw", lang)
        return _l("h2h_fight", lang) if is_individual(sport) else _l("h2h_ot", lang)
    if market == "h2h3":
        return _l("h2h3_fight", lang) if is_individual(sport) else _l("h2h3", lang)
    if market == "dnb":
        return _l("dnb", lang)
    if market == "totals":
        return _l("totals", lang, unit=unit(sport, lang), line=f"{line:g}")
    if market == "spreads":
        if is_individual(sport):
            return _l("spreads_ind", lang, line=_signed(line), unit=unit(sport, lang))
        return _l("spreads", lang, line=_signed(line))
    if market == "btts":
        return _l("btts", lang)
    return market


def _signed(line: float | None) -> str:
    if line is None:
        return ""
    return f"{line:+g}" if line != 0 else "0"


def selection_label(sport: str, market: str, line: float | None, selection: str, home: str, away: str,
                    lang: str = "en") -> str:
    draw = "Uafgjort" if lang == "da" else "Draw"
    if market in ("h2h", "h2h3", "dnb"):
        return {"home": home, "away": away, "draw": draw}.get(selection, selection)
    if market == "totals":
        word = {"over": "Over", "under": "Under"}[selection]
        return f"{word} {line:g}"
    if market == "spreads":
        if selection == "home":
            return f"{home} {_signed(line)}"
        return f"{away} {_signed(-line if line else 0.0)}"
    if market == "btts":
        yes_no = {"yes": ("Yes", "Ja"), "no": ("No", "Nej")}[selection]
        return f"{'Begge scorer' if lang == 'da' else 'BTTS'} {yes_no[1] if lang == 'da' else yes_no[0]}"
    return selection


def swap_sides(market: str, line: float | None, selection: str) -> tuple[float | None, str]:
    """Translate a selection when a provider lists home/away the other way round."""
    swap = {"home": "away", "away": "home"}
    if market == "spreads":
        return (-line if line is not None else None), swap.get(selection, selection)
    if market in ("h2h", "h2h3", "dnb"):
        return line, swap.get(selection, selection)
    return line, selection
