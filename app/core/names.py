"""Team-name normalisation and fuzzy fixture matching.

Different providers spell teams differently ("Man Utd", "Manchester United",
"Manchester United FC").  Matching has to be forgiving but must never merge two
different matches, so a fixture match requires BOTH team names to be similar
and the kick-off times to be close.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Iterable, Protocol

# Tokens that carry no identity information.
_NOISE = {
    "fc", "cf", "afc", "sc", "ac", "as", "ss", "ssc", "us", "fk", "sk", "bk", "if", "ik", "ks",
    "club", "the", "de", "calcio", "hc", "hk", "hb", "thw", "bc", "kk", "bk", "cb",
    "handball", "handbal", "basket", "basketball", "football", "futbol", "fussball",
    "sv", "vfl", "vfb", "tsg", "tsv", "rc", "rcd", "cd", "ud", "sd", "ca", "ogc", "losc",
    "and", "&",
}
_ALIASES = {
    "utd": "united",
    "man": "manchester",
    "st": "saint",
    "intl": "international",
    "internazionale": "inter",
    "psg": "paris saint germain",
    "spurs": "tottenham",
    "wolves": "wolverhampton",
    "bayern munchen": "bayern munich",
    "munchen": "munich",
    "koln": "cologne",
    "nurnberg": "nuremberg",
    "sporting cp": "sporting",
    "olympiacos piraeus": "olympiacos",
    "olympiakos": "olympiacos",
    "olympique lyonnais": "lyon",
    "lyonnais": "lyon",
    "olympique marseille": "marseille",
    "olympique de marseille": "marseille",
    "la": "los angeles",
    "ny": "new york",
}


def strip_accents(text: str) -> str:
    text = text.replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("Æ", "AE")
    text = text.replace("ß", "ss").replace("ł", "l").replace("Ł", "L")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalise_team(name: str) -> str:
    text = strip_accents(name or "").lower()
    text = re.sub(r"[^a-z0-9& ]+", " ", text)
    text = " ".join(text.split())
    for alias, full in _ALIASES.items():
        text = re.sub(rf"\b{re.escape(alias)}\b", full, text)
    tokens = [t for t in text.split() if t not in _NOISE]
    return " ".join(tokens) if tokens else text


def slug(text: str) -> str:
    norm = normalise_team(text)
    return re.sub(r"[^a-z0-9]+", "-", norm).strip("-")[:40] or "x"


def _related(x: str, y: str) -> bool:
    return x == y or (min(len(x), len(y)) >= 3 and (x.startswith(y) or y.startswith(x)))


def team_similarity(a: str, b: str) -> float:
    """0..1 similarity between two team names.

    "Bayern" ~ "Bayern Munich" scores high (token containment), while
    "Manchester United" vs "Manchester City" is capped low because each name
    has a distinctive token the other lacks.
    """
    na, nb = normalise_team(a), normalise_team(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    sa, sb = set(na.split()), set(nb.split())
    score = SequenceMatcher(None, na, nb).ratio()
    shorter, longer = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    matched = [t for t in shorter if any(_related(t, u) for u in longer)]
    if shorter and len(matched) == len(shorter):
        distinct = sum(len(t) for t in matched)
        if distinct >= 4:
            score = max(score, 0.9 if shorter <= longer else 0.84)
        else:
            score = max(score, 0.7)
    only_a = [t for t in sa if not any(_related(t, u) for u in sb)]
    only_b = [t for t in sb if not any(_related(t, u) for u in sa)]
    if only_a and only_b:
        score = min(score, 0.6)
    return score


class _FixtureLike(Protocol):
    sport: str
    home: str
    away: str
    kickoff: datetime


def fixture_match_score(home: str, away: str, f_home: str, f_away: str) -> tuple[float, bool]:
    """Return (score, swapped).  score is the weaker of the two team similarities."""
    straight = min(team_similarity(home, f_home), team_similarity(away, f_away))
    swapped = min(team_similarity(home, f_away), team_similarity(away, f_home))
    if swapped > straight + 0.05:
        return swapped, True
    return straight, False


def find_fixture(
    sport: str,
    home: str,
    away: str,
    kickoff: datetime | None,
    fixtures: Iterable[_FixtureLike],
    *,
    threshold: float = 0.78,
    tolerance: timedelta = timedelta(hours=6),
):
    """Best matching fixture or None.  Returns (fixture, score, swapped)."""
    best = None
    for fx in fixtures:
        if fx.sport != sport:
            continue
        if kickoff is not None and abs(fx.kickoff - kickoff) > tolerance:
            continue
        score, swapped = fixture_match_score(home, away, fx.home, fx.away)
        if score >= threshold and (best is None or score > best[1]):
            best = (fx, score, swapped)
    return best


def search_score(query: str, home: str, away: str) -> float:
    """Score for free-text fixture search ("arsenal chel", "Arsenal - Chelsea")."""
    q = normalise_team(query.replace(" - ", " ").replace(" vs ", " ").replace(" v ", " "))
    if not q:
        return 0.0
    target = f"{normalise_team(home)} {normalise_team(away)}"
    q_tokens = q.split()
    t_tokens = target.split()
    hits = 0.0
    for qt in q_tokens:
        best = 0.0
        for tt in t_tokens:
            if tt.startswith(qt) or qt.startswith(tt):
                best = 1.0
                break
            best = max(best, SequenceMatcher(None, qt, tt).ratio())
        hits += best if best >= 0.7 else 0.0
    return hits / len(q_tokens)
