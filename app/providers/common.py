"""Helpers shared by the supplementary odds sources (odds-api.io, OddsPapi,
scraper): matching external events to known fixtures and competitions."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

from app.core.markets import swap_sides
from app.core.models import Fixture, Quote
from app.core.names import find_fixture, fixture_squad, split_squad, strip_accents
from app.services.registry import Registry
from app.services.repository import Repository


def parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def num(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def as_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "events", "response", "fixtures", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def upcoming(repo: Repository, now: datetime, hours: int) -> list[Fixture]:
    horizon = now + timedelta(hours=hours)
    return [f for f in repo.fixtures() if f.status == "scheduled" and now < f.kickoff <= horizon]


def _league_norm(text: str) -> str:
    """League names must NOT use the team-name aliases ("La Liga" is not "Los Angeles")."""
    text = strip_accents(text or "").lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(t for t in text.split() if t not in ("fc", "the", "men", "mens"))


def _numbers(text: str) -> list[str]:
    return sorted(re.findall(r"\d+", text))


def match_competition(registry: Registry, sport: str, *names: str, country: str = "",
                      squad: Optional[frozenset] = None) -> Optional[dict]:
    """Enabled registry competition matching a provider's league name (and country, when known).

    Deliberately strict: a wrong league is worse than a missing one.  Two things are never left to the
    spelling score, because one short word is all that separates them from another league:

    * the squad - ``squad`` is the event's women's / youth markers (``names.fixture_squad``; taken from the
      league names when not given) and must equal the competition's: "Bundesliga (W)", or a "Bundesliga" match
      between two "(W)" teams, never resolves to the men's Bundesliga,
    * numbers - "2. Bundesliga" and "Premier League 2" are not "Bundesliga" and "Premier League"."""
    if squad is None:
        squad = frozenset().union(*(split_squad(n, sport, league=True)[1] for n in names))
    best, best_score = None, 0.0
    cn = _league_norm(country)
    for comp in registry.competitions(sport=sport, enabled_only=True):
        cc = _league_norm(comp.get("country", ""))
        if cn and cc not in ("europe", "world", "") and cn != cc:
            continue
        comp_name, comp_squad = split_squad(comp["name"], sport, league=True)
        if comp_squad | split_squad(comp["id"], sport, league=True)[1] != squad:
            continue
        target = _league_norm(comp_name)
        for name in names:
            n = _league_norm(split_squad(name, sport, league=True)[0])
            if not n or _numbers(n) != _numbers(target):
                continue
            if n == target:
                score = 1.0
            else:
                score = SequenceMatcher(None, n, target).ratio()
                if set(target.split()) == set(n.split()):
                    score = max(score, 0.95)
            if score > best_score:
                best, best_score = comp, score
    return best if best_score >= 0.9 else None


def attach(repo: Repository, registry: Registry, provider: str, sport: str, ext_id: str, home: str, away: str,
           kickoff: datetime, league_names: tuple[str, ...], known: list[Fixture], create: bool,
           country: str = "", context: tuple[str, ...] = ()) -> Optional[tuple[Fixture, bool]]:
    """Match an external event to a known fixture, or create it (fallback mode) when its league is enabled.

    ``context`` is anything else the source says about where the event belongs (e.g. the whole league path);
    like ``league_names`` it is read for squad markers, so a women's event stays off men's fixtures and
    competitions, but it is not used to pick the competition."""
    leagues = tuple(x for x in (*league_names, *context) if x)
    hit = find_fixture(sport, home, away, kickoff, known, tolerance=timedelta(hours=3), leagues=leagues)
    if hit:
        fx, _score, swapped = hit
        if fx.refs.get(provider) != ext_id:
            fx.refs[provider] = ext_id
            repo.save_fixture(fx)
        return fx, swapped
    if not create:
        return None
    comp = match_competition(registry, sport, *league_names, country=country,
                             squad=fixture_squad(sport, home, away, leagues))
    if not comp:
        return None
    candidate = Fixture(id="", sport=sport, competition_id=comp["id"], competition_name=comp["name"],
                        home=home, away=away, kickoff=kickoff)
    fx, swapped = repo.merge_fixture(candidate, provider, ext_id)
    known.append(fx)
    return fx, swapped


def make_quotes(fx: Fixture, swapped: bool, items: list[tuple], bookmaker: str, source: str, now: datetime,
                updated: Optional[datetime] = None) -> list[Quote]:
    out: dict[str, Quote] = {}
    for market, line, sel, odds in items:
        if swapped:
            line, sel = swap_sides(market, line, sel)
        try:
            q = Quote(fixture_id=fx.id, market=market, line=line, selection=sel, bookmaker=bookmaker, odds=odds,
                      source=source, fetched_at=now, book_updated_at=updated)
        except ValueError:
            continue
        out[q.key] = q
    return list(out.values())
