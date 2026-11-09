"""Sport catalogue: one place that defines every supported sport, its category,
draw rules, score unit and which probability model it uses.

Adding a sport = one entry here (+ a model if none of the existing fit)."""
from __future__ import annotations

CATEGORIES = {
    "team": {"en": "Team sports", "da": "Holdsport", "order": 1},
    "combat": {"en": "Combat sports", "da": "Kampsport", "order": 2},
    "racket": {"en": "Racket sports", "da": "Ketchersport", "order": 3},
}

# draw: whether the main "h2h" market includes a draw (regular-time result)
SPORTS: dict[str, dict] = {
    "football": {"en": "Football", "da": "Fodbold", "category": "team", "icon": "⚽", "draw": True,
                 "unit": ("goals", "mål"), "model": "football",
                 "markets": ("h2h", "dnb", "totals", "spreads", "btts")},
    "basketball": {"en": "Basketball", "da": "Basketball", "category": "team", "icon": "🏀", "draw": False,
                   "unit": ("points", "point"), "model": "basketball",
                   "markets": ("h2h", "h2h3", "totals", "spreads")},
    "handball": {"en": "Handball", "da": "Håndbold", "category": "team", "icon": "🤾", "draw": True,
                 "unit": ("goals", "mål"), "model": "handball",
                 "markets": ("h2h", "dnb", "totals", "spreads")},
    "icehockey": {"en": "Ice hockey", "da": "Ishockey", "category": "team", "icon": "🏒", "draw": False,
                  "unit": ("goals", "mål"), "model": "icehockey",
                  "markets": ("h2h", "h2h3", "totals", "spreads")},
    "americanfootball": {"en": "American football", "da": "Amerikansk fodbold", "category": "team", "icon": "🏈",
                         "draw": False, "unit": ("points", "point"), "model": "americanfootball",
                         "markets": ("h2h", "totals", "spreads")},
    "baseball": {"en": "Baseball", "da": "Baseball", "category": "team", "icon": "⚾", "draw": False,
                 "unit": ("runs", "runs"), "model": "baseball", "markets": ("h2h", "totals", "spreads")},
    "mma": {"en": "MMA / UFC", "da": "MMA / UFC", "category": "combat", "icon": "🥋", "draw": False,
            "unit": ("rounds", "runder"), "model": "binary", "markets": ("h2h", "totals")},
    "boxing": {"en": "Boxing", "da": "Boksning", "category": "combat", "icon": "🥊", "draw": False,
               "unit": ("rounds", "runder"), "model": "binary", "markets": ("h2h", "h2h3", "totals")},
    "tennis": {"en": "Tennis", "da": "Tennis", "category": "racket", "icon": "🎾", "draw": False,
               "unit": ("games", "partier"), "model": "binary", "markets": ("h2h", "totals", "spreads")},
}


def is_sport(value: str) -> bool:
    return value in SPORTS


def has_draw(sport: str) -> bool:
    return bool(SPORTS.get(sport, {}).get("draw"))


def is_individual(sport: str) -> bool:
    """Combat and racket sports: two competitors, no home advantage."""
    return SPORTS.get(sport, {}).get("category") in ("combat", "racket")


def sport_label(sport: str, lang: str = "en") -> str:
    s = SPORTS.get(sport)
    return s.get(lang, s["en"]) if s else sport


def unit(sport: str, lang: str = "en") -> str:
    s = SPORTS.get(sport)
    if not s:
        return "points"
    return s["unit"][1] if lang == "da" else s["unit"][0]


def catalogue() -> dict:
    return {"categories": CATEGORIES,
            "sports": {k: {kk: vv for kk, vv in v.items() if kk != "model"} for k, v in SPORTS.items()}}
