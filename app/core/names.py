"""Team-name normalisation and fuzzy fixture matching.

Different providers spell teams differently ("Man Utd", "Manchester United",
"Manchester United FC").  Matching has to be forgiving but must never merge two
different matches, so a fixture match requires BOTH team names to be similar
and the kick-off times to be close.

Three rules sit on top of the name comparison:

* squad markers - a women's side ("(W)", "Women", "Frauen", "(F)", ...) or an
  age group ("U21") is a different team than the club's first team.  The marker
  has to be the same on both sides, whether it comes from the team names or
  from the league.
* weak side - sponsors and cities come and go in club names ("Partizan
  Belgrade" / "KK Partizan NIS").  When the opponent is exactly the same, the
  kick-off is the same and the two spellings share a distinctive word, it is
  one match: a team cannot play two matches at the same time.
* nicknames belong to a sport - "Spurs" is Tottenham in football only.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable, Optional, Protocol

from app.core.sports import is_individual

# Tokens that carry no identity information.
_NOISE = {
    "fc", "cf", "afc", "sc", "ac", "as", "ss", "ssc", "us", "fk", "sk", "bk", "if", "ik", "ks",
    "club", "the", "de", "calcio", "hc", "hk", "hb", "thw", "bc", "kk", "bk", "cb",
    "handball", "handbal", "basket", "basketball", "football", "futbol", "fussball",
    "sv", "vfl", "vfb", "tsg", "tsv", "rc", "rcd", "cd", "ud", "sd", "ca", "ogc", "losc",
    "and", "&",
}
# Aliases that hold in every sport.
_ALIASES = {
    "utd": "united",
    "man": "manchester",
    "st": "saint",
    "intl": "international",
    "internazionale": "inter",
    "psg": "paris saint germain",
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
# Nicknames that are another club in another sport ("San Antonio Spurs", "Chicago Wolves").
_SPORT_ALIASES = {
    "football": {"spurs": "tottenham", "wolves": "wolverhampton"},
}

# --- squad markers -------------------------------------------------------------
# "(W)", "(F)", "(K)", "(D)": women / feminin / kvinder / Damen.  A single letter only counts in brackets
# (or, for "W", at the very end of a team name: "Chelsea W") - "Group F" and "K League" are something else.
_WOMEN_BRACKET = re.compile(r"[(\[]\s*[wfkd]\s*[)\]]")
_WOMEN_WORDS = (r"women(?: s)?|womens|ladies|frauen|damen|damer|kvinder|kvinner|vrouwen|femenin[oa]|feminin[oa]|"
                r"feminine?s?|femminile|femenil|femeni|wfc")
_WOMEN_TEAM = re.compile(rf"\b(?:{_WOMEN_WORDS})\b|(?<=\w) w$")
_WOMEN_LEAGUE = re.compile(rf"\b(?:{_WOMEN_WORDS}|w)\b")
# leagues that are women's leagues as one word ("Kvindeligaen", "Damallsvenskan", "WNBA"): nothing to strip
_WOMEN_LEAGUE_NAME = re.compile(r"\b(?:kvinde|kvinne|dame)\w*|\b(?:damallsvenskan|wnba|nwsl|wsl)\b")
_YOUTH = re.compile(r"\b(?:u|under|sub) ?(1[4-9]|2[0-3])\b")

# --- weak side -----------------------------------------------------------------
# Two spellings of one club must be listed for the same minute (sources round differently, hence the slack).
SAME_KICKOFF = timedelta(minutes=5)
# Words shared by many different clubs: not enough to call two spellings the same club.
_GENERIC = {
    "real", "atletico", "athletic", "atletic", "sporting", "sport", "united", "city", "town", "county", "rovers",
    "wanderers", "racing", "olympique", "olympic", "olimpia", "dynamo", "dinamo", "lokomotiv", "lokomotiva", "spartak",
    "union", "deportivo", "deportes", "nacional", "national", "inter", "internacional", "royal", "saint", "santa",
    "team", "academy", "university", "college", "state", "north", "south", "east", "west", "angeles", "york",
}


def strip_accents(text: str) -> str:
    text = text.replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("Æ", "AE")
    text = text.replace("ß", "ss").replace("ł", "l").replace("Ł", "L")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _clean(text: str) -> str:
    text = strip_accents(text or "").lower()
    return " ".join(re.sub(r"[^a-z0-9& ]+", " ", text).split())


def normalise_team(name: str, sport: Optional[str] = None) -> str:
    """Comparable form of a team name.  ``sport`` selects the nicknames that apply; without it (free-text
    search, sport unknown) every alias is applied."""
    text = _clean(name)
    aliases = dict(_ALIASES)
    for key, extra in _SPORT_ALIASES.items():
        if sport is None or sport == key:
            aliases.update(extra)
    for alias, full in aliases.items():
        text = re.sub(rf"\b{re.escape(alias)}\b", full, text)
    tokens = [t for t in text.split() if t not in _NOISE]
    return " ".join(tokens) if tokens else text


def slug(text: str, sport: Optional[str] = None) -> str:
    norm = normalise_team(text, sport)
    return re.sub(r"[^a-z0-9]+", "-", norm).strip("-")[:40] or "x"


@lru_cache(maxsize=8192)
def _split_squad(name: str, league: bool) -> tuple[str, frozenset[str]]:
    text = strip_accents(name).lower()
    marks = set()
    text, found = _WOMEN_BRACKET.subn(" ", text)
    if found:
        marks.add("women")
    text = _clean(text)
    rest, found = (_WOMEN_LEAGUE if league else _WOMEN_TEAM).subn(" ", text)
    if found or (league and _WOMEN_LEAGUE_NAME.search(rest)):
        marks.add("women")
    youth = _YOUTH.search(rest)
    if youth:
        marks.add(f"u{youth.group(1)}")
        rest = _YOUTH.sub(" ", rest)
    rest = " ".join(rest.split())
    return rest or text, frozenset(marks)


def split_squad(name: str, sport: Optional[str] = None, *, league: bool = False) -> tuple[str, frozenset[str]]:
    """Split the squad markers off a team name (or, with ``league=True``, a league name or id).

    "1. FC Köln (W)" -> ("1 fc koln", {"women"}), "Denmark U-21" -> ("denmark", {"u21"}),
    "Frauen-Bundesliga" -> ("bundesliga", {"women"}).  Individual sports have no squads: the names are people."""
    if sport and is_individual(sport):
        return name or "", frozenset()
    return _split_squad(name or "", league)


def fixture_squad(sport: Optional[str], home: str, away: str, leagues: Iterable[str] = ()) -> frozenset[str]:
    """Squad markers of a match: those of either team name plus those of its league name(s)."""
    marks = set(split_squad(home, sport)[1]) | split_squad(away, sport)[1]
    for text in leagues:
        marks |= split_squad(text, sport, league=True)[1]
    return frozenset(marks)


def _related(x: str, y: str) -> bool:
    return x == y or (min(len(x), len(y)) >= 3 and (x.startswith(y) or y.startswith(x)))


def team_similarity(a: str, b: str, sport: Optional[str] = None, *, ignore_squad: bool = False) -> float:
    """0..1 similarity between two team names.

    "Bayern" ~ "Bayern Munich" scores high (token containment), while
    "Manchester United" vs "Manchester City" is capped low because each name
    has a distinctive token the other lacks.  A women's or youth side never
    matches another squad of the same club ("Arsenal Women" vs "Arsenal" = 0);
    ``ignore_squad`` compares the club names only, for callers that already know
    both names belong to the same match.
    """
    base_a, squad_a = split_squad(a, sport)
    base_b, squad_b = split_squad(b, sport)
    if squad_a != squad_b and not ignore_squad:
        return 0.0
    na, nb = normalise_team(base_a, sport), normalise_team(base_b, sport)
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


def fixture_match_score(home: str, away: str, f_home: str, f_away: str, sport: Optional[str] = None, *,
                        ignore_squad: bool = False) -> tuple[float, bool]:
    """Return (score, swapped).  score is the weaker of the two team similarities."""
    def sim(x: str, y: str) -> float:
        return team_similarity(x, y, sport, ignore_squad=ignore_squad)

    straight = min(sim(home, f_home), sim(away, f_away))
    swapped = min(sim(home, f_away), sim(away, f_home))
    if swapped > straight + 0.05:
        return swapped, True
    return straight, False


def _club(name: str, sport: Optional[str]) -> str:
    return normalise_team(split_squad(name, sport)[0], sport)


def _shares_distinctive_word(a: str, b: str, sport: Optional[str]) -> bool:
    ta, tb = _club(a, sport).split(), _club(b, sport).split()
    return any(_related(t, u) and min(len(t), len(u)) >= 4 and t not in _GENERIC and u not in _GENERIC
               for t in ta for u in tb)


def weak_side_score(home: str, away: str, f_home: str, f_away: str,
                    sport: Optional[str] = None) -> Optional[tuple[float, bool]]:
    """(score, swapped) when one team is exactly the same and the other shares a distinctive word, else None.

    Only meaningful for two events at the same kick-off (see ``find_fixture``): the same team cannot be in two
    matches at once, so "Partizan Belgrade" and "KK Partizan NIS" against the same opponent are one club.
    The score is the plain similarity of the weak side (below the normal threshold by definition)."""
    best: Optional[tuple[float, bool]] = None
    for swapped, (fh, fa) in ((False, (f_home, f_away)), (True, (f_away, f_home))):
        for exact, weak in (((home, fh), (away, fa)), ((away, fa), (home, fh))):
            same = _club(exact[0], sport)
            if not same or same != _club(exact[1], sport) or not _shares_distinctive_word(*weak, sport):
                continue
            score = team_similarity(*weak, sport, ignore_squad=True)
            if best is None or score > best[0]:
                best = (score, swapped)
    return best


def find_fixture(
    sport: str,
    home: str,
    away: str,
    kickoff: datetime | None,
    fixtures: Iterable[_FixtureLike],
    *,
    threshold: float = 0.78,
    tolerance: timedelta = timedelta(hours=6),
    leagues: Iterable[str] = (),
):
    """Best matching fixture or None.  Returns (fixture, score, swapped).

    ``leagues`` are the league names the source gives for this event; they are only read for squad markers
    ("Bundesliga (W)").  A women's or youth match never matches another squad's fixture.  A source that
    gives no league cannot be judged that way: it is then compared on the team names alone.

    A fixture whose two names both match always wins.  Failing that, a fixture at the same kick-off with one
    exact team and a recognisable other team is accepted (team sports only, see ``weak_side_score``)."""
    leagues = tuple(x for x in leagues if x)
    named = fixture_squad(sport, home, away)
    squad = fixture_squad(sport, home, away, leagues)
    best = weak = None
    for fx in fixtures:
        if fx.sport != sport:
            continue
        if kickoff is not None and abs(fx.kickoff - kickoff) > tolerance:
            continue
        fx_leagues = (getattr(fx, "competition_name", ""), getattr(fx, "competition_id", ""))
        if squad != fixture_squad(sport, fx.home, fx.away, fx_leagues) and (
                leagues or named != fixture_squad(sport, fx.home, fx.away)):
            continue
        score, swapped = fixture_match_score(home, away, fx.home, fx.away, sport, ignore_squad=True)
        if score >= threshold:
            if best is None or score > best[1]:
                best = (fx, score, swapped)
        elif (best is None and kickoff is not None and abs(fx.kickoff - kickoff) <= SAME_KICKOFF
              and not is_individual(sport)):
            hit = weak_side_score(home, away, fx.home, fx.away, sport)
            if hit and (weak is None or hit[0] > weak[1]):
                weak = (fx, hit[0], hit[1])
    return best or weak


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
