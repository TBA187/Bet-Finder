"""OddsPapi (https://oddspapi.io) - free plan: 250 requests per month, all
300+ bookmakers (bet365, Pinnacle, Eurobet, ...), no credit card.

Because the free budget is small it is used in two ways:
* fallback: when The Odds API has no key or its credits are used up, a
  daily share of the monthly budget refreshes the soonest matches,
* on demand: the "Fetch more bookmakers" button on a match (1-2 requests).

Only the main result markets are parsed (market 101 = 1X2, 131 = moneyline);
OddsPapi's IDs for totals/handicaps are not documented publicly.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta

import httpx

from app.core.models import Fixture
from app.core.sports import has_draw
from app.providers.common import as_list, attach, make_quotes, num, parse_time, upcoming
from app.providers.http import CachedClient, ProviderError
from app.services.registry import Registry
from app.services.repository import Repository

log = logging.getLogger("betfinder.oddspapi")
BASE = "https://api.oddspapi.io/v4"
NAME = "oddspapi"
FREE_LIMIT = 250
SPORT_IDS = {"football": 10, "basketball": 11, "tennis": 12, "baseball": 13, "americanfootball": 14, "icehockey": 15}
SPORT_SLUGS = {"handball": "handball", "mma": "mma", "boxing": "boxing"}


class OddsPapiProvider:
    name = NAME
    label = "OddsPapi · oddspapi.io (free)"
    method = "api"

    def __init__(self, repo: Repository, registry: Registry, settings: dict):
        self.repo, self.registry, self.settings = repo, registry, settings
        self.key = settings.get("oddspapi_key", "")

    def configured(self) -> bool:
        return bool(self.key)

    # ------------------------------------------------------------- budget
    def used_this_month(self, now: datetime) -> int:
        st = self.repo.provider_status().get(NAME, {})
        return int(st.get("requests_by_month", {}).get(now.strftime("%Y-%m"), 0))

    def budget_left(self, now: datetime) -> int:
        return max(0, int(self.settings.get("oddspapi_monthly_budget", 240)) - self.used_this_month(now))

    def daily_allowance(self, now: datetime) -> int:
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        days_left = days_in_month - now.day + 1
        return max(1, self.budget_left(now) // max(days_left, 1))

    def _client(self, client) -> CachedClient:
        return CachedClient(self.repo, NAME, client, (self.key,))

    async def _sport_id(self, cc: CachedClient, sport: str):
        if sport in SPORT_IDS:
            return SPORT_IDS[sport]
        data = await cc.get_json(f"{BASE}/sports", params={"apiKey": self.key}, ttl_seconds=30 * 86400)
        for item in as_list(data):
            if str(item.get("slug", "")).lower().startswith(SPORT_SLUGS.get(sport, sport)):
                return item.get("sportId")
        return None

    async def _fixtures(self, cc: CachedClient, sport: str, now: datetime) -> list[dict]:
        sid = await self._sport_id(cc, sport)
        if sid is None:
            return []
        params = {"apiKey": self.key, "sportId": sid, "from": now.strftime("%Y-%m-%d"),
                  "to": (now + timedelta(days=3)).strftime("%Y-%m-%d")}
        try:
            return as_list(await cc.get_json(f"{BASE}/fixtures", params=params, ttl_seconds=12 * 3600))
        except ProviderError as exc:
            if "404" in str(exc):
                return []  # OddsPapi answers 404 for an empty window
            raise

    async def _odds(self, cc: CachedClient, fx: Fixture, ext_id: str, swapped: bool, now: datetime) -> int:
        books = ",".join(self.settings.get("oddspapi_bookmakers") or [])
        data = await cc.get_json(f"{BASE}/odds", params={"apiKey": self.key, "fixtureId": ext_id, "bookmakers": books},
                                 ttl_seconds=900)
        quotes = []
        for slug, bdata in ((data or {}).get("bookmakerOdds") or {}).items():
            reg = self.registry.from_name(slug)
            items = self.parse_markets(bdata.get("markets") or {}, fx.sport)
            quotes += make_quotes(fx, swapped, items, reg, NAME, now)
        self.repo.upsert_quotes(fx.id, quotes, replace_source=NAME)
        return len(quotes)

    @staticmethod
    def parse_markets(markets: dict, sport: str) -> list[tuple]:
        def price(outcomes, oid):
            o = (outcomes or {}).get(oid) or {}
            p = (o.get("players") or {}).get("0") or {}
            if isinstance(p, list):
                p = p[-1] if p else {}
            return num(p.get("price")) if p.get("active", True) else None

        out = []
        m101 = (markets.get("101") or {}).get("outcomes")
        if m101:
            market = "h2h" if has_draw(sport) else "h2h3"
            out += [(market, None, "home", price(m101, "101")), (market, None, "draw", price(m101, "102")),
                    (market, None, "away", price(m101, "103"))]
        m131 = (markets.get("131") or {}).get("outcomes")
        if m131:
            market = "dnb" if has_draw(sport) else "h2h"
            out += [(market, None, "home", price(m131, "131")), (market, None, "away", price(m131, "132"))]
        return [x for x in out if x[3] is not None]

    # ------------------------------------------------------------ modes
    async def refresh(self, client: httpx.AsyncClient, competitions: list[dict], now: datetime) -> dict:
        """Fallback refresh: spend today's share of the monthly free budget on the soonest matches."""
        summary = {"provider": NAME, "method": "api", "fixtures": 0, "quotes": 0, "errors": [], "skipped": []}
        if not self.configured():
            summary["skipped"].append("No API key configured")
            return summary
        allowance = min(self.daily_allowance(now), self.budget_left(now))
        if allowance <= 0:
            summary["exhausted"] = True
            summary["skipped"].append("Monthly free budget used up")
            return summary
        cc = self._client(client)
        known = upcoming(self.repo, now, int(self.settings["lookahead_hours"]))
        targets = []
        for sport in sorted({c["sport"] for c in competitions}):
            try:
                for f in await self._fixtures(cc, sport, now):
                    ko = parse_time(f.get("startTime"))
                    if not ko or not (now < ko <= now + timedelta(hours=int(self.settings["lookahead_hours"]))) or not f.get("hasOdds", True):
                        continue
                    hit = attach(self.repo, self.registry, NAME, sport, str(f.get("fixtureId")), str(f.get("participant1Name", "")),
                                 str(f.get("participant2Name", "")), ko, (str(f.get("tournamentName", "")),), known, create=True)
                    if hit:
                        targets.append((ko, hit[0], str(f.get("fixtureId")), hit[1]))
            except ProviderError as exc:
                summary["errors"].append(f"{sport}: {exc}")
        targets.sort(key=lambda t: t[0])
        spent = 0
        for _ko, fx, ext_id, swapped in targets:
            if spent >= allowance:
                summary["skipped"].append(f"{len(targets) - spent} matches left for later (free budget)")
                break
            try:
                summary["quotes"] += await self._odds(cc, fx, ext_id, swapped, now)
                summary["fixtures"] += 1
                spent += 1
            except ProviderError as exc:
                summary["errors"].append(f"{fx.name}: {exc}")
                break
        self.repo.update_provider_status(NAME, **({"last_ok": now.isoformat()} if not summary["errors"]
                                                  else {"last_error": summary["errors"][-1], "last_error_at": now.isoformat()}))
        return summary

    async def fetch_fixture(self, client: httpx.AsyncClient, fx: Fixture, now: datetime) -> dict:
        """On demand: add all bookmakers OddsPapi has for one match (1-2 requests)."""
        if not self.configured():
            raise ProviderError("OddsPapi key not configured")
        if self.budget_left(now) <= 0:
            raise ProviderError("OddsPapi monthly free budget used up")
        cc = self._client(client)
        ext_id = fx.refs.get(NAME)
        swapped = False
        if not ext_id:
            for f in await self._fixtures(cc, fx.sport, now):
                ko = parse_time(f.get("startTime"))
                if not ko:
                    continue
                hit = attach(self.repo, self.registry, NAME, fx.sport, str(f.get("fixtureId")), str(f.get("participant1Name", "")),
                             str(f.get("participant2Name", "")), ko, (), [fx], create=False,
                             context=(str(f.get("tournamentName", "")),))
                if hit:
                    ext_id, swapped = str(f.get("fixtureId")), hit[1]
                    break
        if not ext_id:
            raise ProviderError("This match was not found at OddsPapi")
        n = await self._odds(cc, fx, ext_id, swapped, now)
        self.repo.update_provider_status(NAME, last_ok=now.isoformat())
        return {"quotes": n, "budget_left": self.budget_left(now)}
