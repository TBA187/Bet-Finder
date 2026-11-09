"""Bookmaker and competition registries (data/registry/*.json).

The registry files are part of the repository.  User choices (which
competitions are enabled) are stored separately in the runtime folder so the
registry can be updated without losing them.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from app.core.sports import SPORTS
from app.storage.json_store import JsonStore

log = logging.getLogger("betfinder.registry")

DEMO_BOOKMAKERS = [
    {"key": "demo_sharp", "name": "Demo Sharp", "type": "sharp"},
    {"key": "demo_book_a", "name": "Demo Book A", "type": "soft"},
    {"key": "demo_book_b", "name": "Demo Book B", "type": "soft"},
    {"key": "demo_book_c", "name": "Demo Book C", "type": "soft"},
    {"key": "demo_book_d", "name": "Demo Book D", "type": "soft"},
    {"key": "demo_book_e", "name": "Demo Book E", "type": "soft"},
]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


class Registry:
    OVERRIDES = "competition_overrides"

    def __init__(self, registry_dir: Path, store: JsonStore):
        self.store = store
        self.errors: list[str] = []
        self._bookmakers = self._load(registry_dir / "bookmakers.json", "bookmakers")
        self._competitions = self._load(registry_dir / "competitions.json", "competitions")
        self._competitions = [c for c in self._competitions if self._valid_competition(c)]
        self.by_key = {b["key"]: b for b in self._bookmakers if "key" in b}
        for demo in DEMO_BOOKMAKERS:
            self.by_key.setdefault(demo["key"], {**demo, "demo": True, "sources": {}})
        self._alias: dict[str, str] = {}
        self._odds_api: dict[str, tuple[str, int]] = {}
        for b in self._bookmakers:
            for alias in [b.get("name", ""), *b.get("aliases", [])]:
                self._alias[_norm(alias)] = b["key"]
            for i, okey in enumerate(b.get("sources", {}).get("the_odds_api", []) or []):
                self._odds_api[okey] = (b["key"], i)
            for src in ("api_sports", "odds_api_io", "oddspapi"):
                for name in b.get("sources", {}).get(src, []) or []:
                    self._alias[_norm(name)] = b["key"]

    def _load(self, path: Path, field: str) -> list[dict]:
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            items = data.get(field, [])
            if not isinstance(items, list):
                raise ValueError(f"'{field}' must be a list")
            return [i for i in items if isinstance(i, dict)]
        except (OSError, ValueError) as exc:
            msg = f"Could not load registry {path.name}: {exc}"
            log.error(msg)
            self.errors.append(msg)
            return []

    def _valid_competition(self, c: dict) -> bool:
        ok = isinstance(c.get("id"), str) and c.get("sport") in SPORTS and isinstance(c.get("name"), str)
        if not ok:
            self.errors.append(f"Invalid competition entry skipped: {c.get('id', c)}")
        return ok

    # -- bookmakers ----------------------------------------------------------
    def bookmakers(self) -> list[dict]:
        return list(self._bookmakers)

    def bookmaker(self, key: str) -> dict:
        b = self.by_key.get(key)
        if b:
            return b
        return {"key": key, "name": key.replace("_", " ").title(), "type": "soft", "registered": False, "sources": {}}

    def bookmaker_name(self, key: str) -> str:
        return self.bookmaker(key).get("name", key)

    def is_sharp(self, key: str) -> bool:
        return self.bookmaker(key).get("type") in ("sharp", "exchange")

    def from_odds_api(self, okey: str, title: str = "") -> tuple[str, int]:
        """Registry key + preference rank for a The Odds API bookmaker key."""
        if okey in self._odds_api:
            return self._odds_api[okey]
        if title and _norm(title) in self._alias:
            return self._alias[_norm(title)], 50
        return re.sub(r"[^a-z0-9_]+", "_", okey.lower()), 99

    def from_name(self, name: str) -> str:
        """Registry key for a free-text bookmaker name (API-Sports, manual entry)."""
        n = _norm(name)
        if n in self._alias:
            return self._alias[n]
        if name in self.by_key:
            return name
        return re.sub(r"[^a-z0-9]+", "_", (name or "unknown").lower()).strip("_") or "unknown"

    # -- competitions ----------------------------------------------------------
    def _overrides(self) -> dict:
        data = self.store.read(self.OVERRIDES, {})
        return data if isinstance(data, dict) else {}

    def competitions(self, sport: Optional[str] = None, enabled_only: bool = False) -> list[dict]:
        overrides = self._overrides()
        out = []
        for c in self._competitions:
            item = {**c, "enabled": bool(overrides.get(c["id"], c.get("enabled", False)))}
            if sport and item["sport"] != sport:
                continue
            if enabled_only and not item["enabled"]:
                continue
            out.append(item)
        return out

    def competition(self, cid: str) -> Optional[dict]:
        for c in self.competitions():
            if c["id"] == cid:
                return c
        return None

    def competition_or_custom(self, cid: str, sport: str, name: str = "") -> dict:
        found = self.competition(cid)
        if found:
            return found
        return {"id": cid, "sport": sport, "name": name or cid, "country": "", "enabled": True, "providers": {}, "params": {}, "custom": True}

    def set_enabled(self, cid: str, enabled: bool) -> None:
        if not any(c["id"] == cid for c in self._competitions):
            raise KeyError(cid)
        with self.store.edit(self.OVERRIDES, {}) as data:
            data[cid] = bool(enabled)
