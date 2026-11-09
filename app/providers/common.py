"""Helpers shared by the supplementary odds sources (odds-api.io, OddsPapi,
scraper): matching external events to known fixtures and competitions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.markets import market_id, swap_sides
from app.core.models import Fixture, Quote
import re
from difflib import SequenceMatcher

from app.core.names import find_fixture, strip_accents
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
    return [f for f in repo.fixtures(include_demo=False) if f.status == "scheduled" and now < f.kickoff <= horizon]


def _league_norm(text: str) -> str:
    """League names must NOT use the team-name aliases ("La Liga" is not "Los Angeles")."""
    text = strip_accents(text or "").lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(t for t in text.split() if t not in ("fc", "the", "men", "mens"))


def match_competition(registry: Registry, sport: str, *names: str, country: str = "") -> Optional[dict]:
    """Enabled registry competition matching a provider's league name (and country, when known).

    Deliberately strict: a wrong league is worse than a missing one."""
    best, best_score = None, 0.0
    cn = _league_norm(country)
    for comp in registry.competitions(sport=sport, enabled_only=True):
        cc = _league_norm(comp.get("country", ""))
        if cn and cc not in ("europe", "world", "") and cn != cc:
            continue
        target = _league_norm(comp["name"])
        for name in names:
            n = _league_norm(name)
            if not n:
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
           country: str = "") -> Optional[tuple[Fixture, bool]]:
    """Match an external event to a known fixture, or create it (fallback mode) when its league is enabled."""
    hit = find_fixture(sport, home, away, kickoff, known, tolerance=timedelta(hours=3))
    if hit:
        fx, _score, swapped = hit
        if fx.refs.get(provider) != ext_id:
            fx.refs[provider] = ext_id
            repo.save_fixture(fx)
        return fx, swapped
    if not create:
        return None
    comp = match_competition(registry, sport, *league_names, country=country)
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


def complete_only(fx: Fixture, quotes: list[Quote]) -> list[Quote]:
    """Drop half markets (a lone 'over' without its 'under' etc.)."""
    from app.core.markets import selections_for
    groups: dict[tuple, list[Quote]] = {}
    for q in quotes:
        groups.setdefault((q.bookmaker, market_id(q.market, q.line)), []).append(q)
    keep = []
    for (_bk, _mid), qs in groups.items():
        need = set(selections_for(fx.sport, qs[0].market))
        if need <= {q.selection for q in qs}:
            keep.extend(qs)
    return keep
