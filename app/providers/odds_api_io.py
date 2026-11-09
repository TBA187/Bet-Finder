"""odds-api.io - the one feed we found that carries Danske Spil ("DanskeSpil")
and Eurobet ("Eurobet IT").  Paid: the Solo plan (about £49/month) includes
2 bookmakers of your choice; free keys are not currently issued to new users.

It is used as a supplement: its events are matched to fixtures we already
know (from The Odds API, API-Sports or manual entry) and the chosen
bookmakers' prices are added to them.
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

from app.core.markets import normalise_line
from app.core.names import team_similarity
from app.core.sports import SPORTS, has_draw
from app.providers.common import as_list, attach, make_quotes, num, parse_time, upcoming
from app.providers.http import CachedClient, ProviderError
from app.services.registry import Registry
from app.services.repository import Repository

log = logging.getLogger("betfinder.odds_api_io")
BASE = "https://api.odds-api.io/v3"
NAME = "odds_api_io"


class OddsApiIoProvider:
    name = NAME
    label = "odds-api.io"
    method = "api"

    def __init__(self, repo: Repository, registry: Registry, settings: dict):
        self.repo, self.registry, self.settings = repo, registry, settings
        self.key = settings.get("odds_api_io_key", "")

    def configured(self) -> bool:
        return bool(self.key)

    def _slugs(self, sports_payload) -> dict[str, str]:
        out = {}
        for item in as_list(sports_payload):
            name = str(item.get("name", "")) if isinstance(item, dict) else str(item)
            slug = str(item.get("slug", name)) if isinstance(item, dict) else str(item)
            for key, meta in SPORTS.items():
                if key in out:
                    continue
                if team_similarity(name, meta["en"]) >= 0.9 or slug.replace("-", "").lower() == key:
                    out[key] = slug
        out.setdefault("football", "football")
        return out

    async def refresh(self, client: httpx.AsyncClient, competitions: list[dict], now: datetime) -> dict:
        summary = {"provider": NAME, "method": "api", "fixtures": 0, "quotes": 0, "errors": [], "skipped": []}
        if not self.configured():
            summary["skipped"].append("No API key configured")
            return summary
        books = [b for b in (self.settings.get("odds_api_io_bookmakers") or []) if b][:10]
        cc = CachedClient(self.repo, NAME, client, (self.key,))
        try:
            slugs = self._slugs(await cc.get_json(f"{BASE}/sports", params={}, ttl_seconds=86400))
        except ProviderError as exc:
            summary["errors"].append(str(exc))
            self.repo.update_provider_status(NAME, last_error=str(exc), last_error_at=now.isoformat())
            return summary
        known = upcoming(self.repo, now, int(self.settings["lookahead_hours"]))
        for sport in sorted({f.sport for f in known}):
            slug = slugs.get(sport)
            if not slug:
                continue
            try:
                events = as_list(await cc.get_json(f"{BASE}/events", params={"apiKey": self.key, "sport": slug}, ttl_seconds=1800))
            except ProviderError as exc:
                summary["errors"].append(f"{sport}: {exc}")
                continue
            matched = {}
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                ko = parse_time(ev.get("date") or ev.get("startTime"))
                home, away = ev.get("home"), ev.get("away")
                if not (ko and home and away and ev.get("id") is not None):
                    continue
                hit = attach(self.repo, self.registry, NAME, sport, str(ev["id"]), str(home), str(away), ko, (), known, create=False)
                if hit:
                    matched[str(ev["id"])] = hit
            ids = list(matched)
            for i in range(0, len(ids), 10):
                chunk = ids[i:i + 10]
                try:
                    data = await cc.get_json(f"{BASE}/odds/multi", params={"apiKey": self.key, "eventIds": ",".join(chunk),
                                                                          "bookmakers": ",".join(books)}, ttl_seconds=600)
                except ProviderError as exc:
                    summary["errors"].append(f"{sport} odds: {exc}")
                    break
                for item in as_list(data) or ([data] if isinstance(data, dict) else []):
                    hit = matched.get(str(item.get("id")))
                    if not hit:
                        continue
                    fx, swapped = hit
                    quotes = []
                    for bname, markets in (item.get("bookmakers") or {}).items():
                        reg = self.registry.from_name(bname)
                        for m in markets or []:
                            items = self.parse_market(m, fx.sport)
                            quotes += make_quotes(fx, swapped, items, reg, NAME, now, parse_time(m.get("updatedAt")))
                    self.repo.upsert_quotes(fx.id, quotes, replace_source=NAME)
                    summary["fixtures"] += 1
                    summary["quotes"] += len(quotes)
        self.repo.update_provider_status(NAME, **({"last_ok": now.isoformat()} if not summary["errors"]
                                                  else {"last_error": summary["errors"][-1], "last_error_at": now.isoformat()}))
        return summary

    @staticmethod
    def parse_market(m: dict, sport: str) -> list[tuple]:
        name = str(m.get("name", "")).strip().lower()
        out = []
        for o in m.get("odds", []) or []:
            if name in ("ml", "moneyline", "1x2", "match winner", "home/away"):
                h, d, a = num(o.get("home")), num(o.get("draw")), num(o.get("away"))
                if d is not None:
                    market = "h2h" if has_draw(sport) else "h2h3"
                    out += [(market, None, "home", h), (market, None, "draw", d), (market, None, "away", a)]
                else:
                    market = "dnb" if has_draw(sport) else "h2h"
                    out += [(market, None, "home", h), (market, None, "away", a)]
            elif name in ("spread", "asian handicap", "handicap"):
                line = normalise_line(o.get("hdp")) if o.get("hdp") is not None else None
                if line is not None:
                    out += [("spreads", line, "home", num(o.get("home"))), ("spreads", line, "away", num(o.get("away")))]
            elif name in ("totals", "total", "over/under"):
                raw = o.get("max", o.get("hdp", o.get("line")))
                line = normalise_line(raw) if raw is not None else None
                if line is not None:
                    out += [("totals", line, "over", num(o.get("over"))), ("totals", line, "under", num(o.get("under")))]
        return [x for x in out if x[3] is not None]
