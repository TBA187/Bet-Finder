"""Typed access to the JSON files in data/runtime.

Files:
    fixtures.json        {fixture_id: Fixture}
    odds.json            {fixture_id: {quote_key: Quote (+history)}}
    team_news.json       manual absences / lineup notes per fixture
    provider_news.json   absences / lineups fetched from providers
    team_stats.json      cached normalised team statistics
    h2h.json             cached head-to-head meetings per fixture
    bets.json            tracked selections / coupons
    daily_picks.json     generated daily coupons per date
    provider_status.json last call, errors and quota per provider
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from pydantic import ValidationError

from app.core.models import Absence, Fixture, LineupInfo, Quote, TeamStats, utcnow
from app.core.names import find_fixture, slug
from app.storage.json_store import JsonStore

log = logging.getLogger("betfinder.repository")

HISTORY_LIMIT = 80


def fixture_id_for(sport: str, home: str, away: str, kickoff: datetime) -> str:
    digest = hashlib.sha1(f"{sport}|{home}|{away}|{kickoff.isoformat()}".encode()).hexdigest()[:6]
    return f"{sport[:2]}-{kickoff:%Y%m%d}-{slug(home)}-{slug(away)}-{digest}"


class Repository:
    def __init__(self, store: JsonStore):
        self.store = store

    # ------------------------------------------------------------------ fixtures
    def _load_fixtures(self) -> dict[str, Fixture]:
        raw = self.store.read("fixtures", {})
        out: dict[str, Fixture] = {}
        if not isinstance(raw, dict):
            return out
        for fid, item in raw.items():
            try:
                out[fid] = Fixture.model_validate(item)
            except ValidationError as exc:
                log.warning("Skipping invalid fixture %s: %s", fid, exc.errors()[:1])
        return out

    def fixtures(self, include_demo: bool = True) -> list[Fixture]:
        items = self._load_fixtures().values()
        return [f for f in items if include_demo or not f.is_demo]

    def fixture(self, fid: str) -> Optional[Fixture]:
        return self._load_fixtures().get(fid)

    def save_fixture(self, fx: Fixture) -> Fixture:
        fx.updated_at = utcnow()
        with self.store.edit("fixtures", {}) as data:
            data[fx.id] = fx.model_dump(mode="json")
        return fx

    def merge_fixture(self, candidate: Fixture, provider: str, provider_id: Optional[str]) -> tuple[Fixture, bool]:
        """Attach a provider's fixture to an existing canonical fixture, or add it.

        Returns (fixture, swapped) where swapped means the provider lists the
        teams the other way round (neutral venues)."""
        with self.store.edit("fixtures", {}) as data:
            existing = {}
            for fid, item in data.items():
                try:
                    existing[fid] = Fixture.model_validate(item)
                except ValidationError:
                    continue
            target: Optional[Fixture] = None
            swapped = False
            if provider_id:
                for fx in existing.values():
                    if fx.refs.get(provider) == provider_id:
                        target = fx
                        break
            if target is None:
                pool = [f for f in existing.values() if f.is_demo == candidate.is_demo]
                hit = find_fixture(candidate.sport, candidate.home, candidate.away, candidate.kickoff, pool)
                if hit:
                    target, _score, swapped = hit
            if target is None:
                target = candidate
                target.id = target.id or fixture_id_for(candidate.sport, candidate.home, candidate.away, candidate.kickoff)
            else:
                # Provider data refreshes kickoff/status of the canonical fixture.
                if provider != "manual":
                    target.kickoff = candidate.kickoff
                    if candidate.status != "unknown":
                        target.status = candidate.status
                    if candidate.score_home is not None and not swapped:
                        target.score_home, target.score_away = candidate.score_home, candidate.score_away
                    elif candidate.score_home is not None and swapped:
                        target.score_home, target.score_away = candidate.score_away, candidate.score_home
                if target.competition_id.startswith("custom") and not candidate.competition_id.startswith("custom"):
                    target.competition_id, target.competition_name = candidate.competition_id, candidate.competition_name
            if provider_id:
                target.refs[provider] = provider_id
            for prov, teams in candidate.team_refs.items():
                target.team_refs[prov] = {"home": teams.get("away"), "away": teams.get("home")} if swapped else teams
            target.league_refs.update(candidate.league_refs)
            target.updated_at = utcnow()
            data[target.id] = target.model_dump(mode="json")
            return target, swapped

    def set_result(self, fid: str, score_home: int, score_away: int, status: str = "finished") -> Fixture:
        with self.store.edit("fixtures", {}) as data:
            if fid not in data:
                raise KeyError(fid)
            fx = Fixture.model_validate(data[fid])
            fx.score_home, fx.score_away, fx.status = int(score_home), int(score_away), status
            fx.updated_at = utcnow()
            data[fid] = fx.model_dump(mode="json")
            return fx

    def delete_fixtures(self, ids: Iterable[str]) -> int:
        ids = set(ids)
        if not ids:
            return 0
        removed = 0
        with self.store.edit("fixtures", {}) as data:
            for fid in ids:
                if data.pop(fid, None) is not None:
                    removed += 1
        for name in ("odds", "team_news", "provider_news", "h2h"):
            with self.store.edit(name, {}, pretty=name not in ("odds",)) as data:
                for fid in ids:
                    data.pop(fid, None)
        return removed

    # -------------------------------------------------------------------- quotes
    def quotes(self, fid: str) -> list[Quote]:
        return self._parse_quotes(self.store.read("odds", {}).get(fid, {}))

    def all_quotes(self) -> dict[str, list[Quote]]:
        raw = self.store.read("odds", {})
        return {fid: self._parse_quotes(items) for fid, items in raw.items()} if isinstance(raw, dict) else {}

    @staticmethod
    def _parse_quotes(items: dict) -> list[Quote]:
        out = []
        for item in (items or {}).values():
            try:
                out.append(Quote.model_validate(item))
            except ValidationError:
                continue
        return out

    def upsert_quotes(self, fid: str, quotes: list[Quote], replace_source: Optional[str] = None) -> int:
        """Store current prices, appending to each price's history when it changes.

        ``replace_source`` removes this source's quotes for the fixture that
        were not part of this update (market suspended / bookmaker removed)."""
        changed = 0
        with self.store.edit("odds", {}, pretty=False) as data:
            book = data.setdefault(fid, {})
            seen = set()
            for q in quotes:
                key = q.key
                seen.add(key)
                old = book.get(key)
                history = list(old.get("history", [])) if old else []
                if not history or abs(float(history[-1][1]) - q.odds) > 1e-9:
                    history.append([q.fetched_at.isoformat().replace("+00:00", "Z"), q.odds])
                    changed += 1
                item = q.model_dump(mode="json")
                item["history"] = history[-HISTORY_LIMIT:]
                book[key] = item
            if replace_source:
                for key in [k for k, v in book.items() if v.get("source") == replace_source and k not in seen]:
                    del book[key]
        return changed

    def delete_quotes(self, fid: str, source: Optional[str] = None, bookmaker: Optional[str] = None) -> int:
        removed = 0
        with self.store.edit("odds", {}, pretty=False) as data:
            book = data.get(fid, {})
            for key in list(book):
                v = book[key]
                if (source is None or v.get("source") == source) and (bookmaker is None or v.get("bookmaker") == bookmaker):
                    del book[key]
                    removed += 1
        return removed

    # ---------------------------------------------------------------- team news
    def team_news(self, fid: str) -> dict:
        raw = self.store.read("team_news", {}).get(fid, {})
        absences = []
        for a in raw.get("absences", []):
            try:
                absences.append(Absence.model_validate(a))
            except ValidationError:
                continue
        lineups = {}
        for side in ("home", "away"):
            try:
                lineups[side] = LineupInfo.model_validate(raw.get("lineups", {}).get(side, {}))
            except ValidationError:
                lineups[side] = LineupInfo()
        return {"absences": absences, "lineups": lineups, "xg": raw.get("xg", {})}

    def add_absence(self, fid: str, absence: Absence) -> Absence:
        absence.id = absence.id or uuid.uuid4().hex[:10]
        with self.store.edit("team_news", {}) as data:
            entry = data.setdefault(fid, {})
            entry.setdefault("absences", []).append(absence.model_dump(mode="json"))
        return absence

    def remove_absence(self, fid: str, absence_id: str) -> bool:
        with self.store.edit("team_news", {}) as data:
            entry = data.get(fid, {})
            before = len(entry.get("absences", []))
            entry["absences"] = [a for a in entry.get("absences", []) if a.get("id") != absence_id]
            return len(entry["absences"]) != before

    def set_lineup(self, fid: str, side: str, info: LineupInfo) -> LineupInfo:
        with self.store.edit("team_news", {}) as data:
            entry = data.setdefault(fid, {})
            entry.setdefault("lineups", {})[side] = info.model_dump(mode="json")
        return info

    def set_xg(self, fid: str, side: str, xg_for: Optional[float], xg_against: Optional[float], games: int = 6) -> None:
        with self.store.edit("team_news", {}) as data:
            entry = data.setdefault(fid, {}).setdefault("xg", {})
            if xg_for is None or xg_against is None:
                entry.pop(side, None)
            else:
                entry[side] = {"for": float(xg_for), "against": float(xg_against), "games": int(games)}

    def provider_news(self, fid: str) -> dict:
        return self.store.read("provider_news", {}).get(fid, {})

    def save_provider_news(self, fid: str, patch: dict) -> None:
        with self.store.edit("provider_news", {}) as data:
            data.setdefault(fid, {}).update(patch)

    # -------------------------------------------------------------------- stats
    def team_stats(self, key: str) -> Optional[TeamStats]:
        raw = self.store.read("team_stats", {}).get(key)
        if not raw:
            return None
        try:
            return TeamStats.model_validate(raw)
        except ValidationError:
            return None

    def save_team_stats(self, key: str, stats: TeamStats) -> None:
        with self.store.edit("team_stats", {}, pretty=False) as data:
            data[key] = stats.model_dump(mode="json")

    def h2h(self, fid: str) -> Optional[list[dict]]:
        return self.store.read("h2h", {}).get(fid)

    def save_h2h(self, fid: str, meetings: list[dict]) -> None:
        with self.store.edit("h2h", {}) as data:
            data[fid] = meetings

    # ---------------------------------------------------------- provider status
    def provider_status(self) -> dict:
        data = self.store.read("provider_status", {})
        return data if isinstance(data, dict) else {}

    def update_provider_status(self, provider: str, **fields) -> None:
        with self.store.edit("provider_status", {}) as data:
            entry = data.setdefault(provider, {})
            entry.update(fields)
            entry["updated_at"] = utcnow().isoformat()

    def count_provider_request(self, provider: str, n: int = 1) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.store.edit("provider_status", {}) as data:
            entry = data.setdefault(provider, {})
            counts = entry.setdefault("requests_by_day", {})
            counts[today] = counts.get(today, 0) + n
            for day in sorted(counts)[:-35]:
                counts.pop(day, None)
            month = today[:7]
            months = entry.setdefault("requests_by_month", {})
            months[month] = months.get(month, 0) + n
            for m in sorted(months)[:-6]:
                months.pop(m, None)
            entry["last_request_at"] = utcnow().isoformat()
            return counts[today]

    # --------------------------------------------------------------- housekeeping
    def prune(self, retention_days: int, keep: set[str]) -> int:
        cutoff = utcnow() - timedelta(days=retention_days)
        old = [f.id for f in self.fixtures() if f.kickoff < cutoff and f.id not in keep]
        return self.delete_fixtures(old)

    def remove_demo_data(self) -> int:
        return self.delete_fixtures([f.id for f in self.fixtures() if f.is_demo])
