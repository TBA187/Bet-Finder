"""Scraping fallback - used only when the API sources are unavailable or
their free credits are used up (or always, if you choose so in Sources).

It is deliberately POLITE and will never try to get around a website's
protection:
* robots.txt is checked before every host is read; a disallowed path is skipped,
* at least ``scraper_min_interval_seconds`` between requests to the same host,
* an honest User-Agent, no proxies, no headless browser, no CAPTCHA solving,
* HTTP 401/403/429/503 marks the host as "blocked" for 24 hours and stops.

Adapters read the public JSON that some bookmaker websites load in the
browser.  The first adapter covers the Kambi sportsbook platform used by
several European brands (e.g. Unibet).  Many bookmakers (including Danske
Spil) do not expose such data or block automated access - they then simply
show as "blocked/unavailable" in the Data usage panel.

Scraping may conflict with a bookmaker's terms of use.  It is your choice to
enable it (setting ``scraper_mode``); scraped prices are always labelled.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.config import APP_VERSION
from app.core.markets import normalise_line
from app.core.models import utcnow
from app.core.sports import has_draw
from app.providers.common import attach, make_quotes, parse_time, upcoming
from app.services.registry import Registry
from app.services.repository import Repository

log = logging.getLogger("betfinder.scraper")
NAME = "scraper"
USER_AGENT = f"BetFinderBot/{APP_VERSION} (personal odds comparison; respects robots.txt)"
BLOCK_CODES = (401, 403, 429, 503)
KAMBI_SPORTS = {"football": "football", "basketball": "basketball", "handball": "handball", "icehockey": "ice_hockey",
                "tennis": "tennis", "mma": "ufc_mma", "boxing": "boxing", "americanfootball": "american_football",
                "baseball": "baseball"}
KAMBI_BRANDS = {"ub": "unibet", "ubdk": "unibet", "ubse": "unibet", "paf": "paf", "888": "sport888", "leo": "leovegas",
                "atg": "atg", "svenskaspel": "svenskaspel"}


class ScrapeBlocked(Exception):
    pass


class PoliteFetcher:
    def __init__(self, repo: Repository, client: httpx.AsyncClient, min_interval: float):
        self.repo, self.client, self.min_interval = repo, client, min_interval
        self._robots: dict[str, Optional[RobotFileParser]] = {}
        self._last: dict[str, float] = {}

    def _blocked_until(self, host: str) -> Optional[datetime]:
        until = self.repo.provider_status().get(NAME, {}).get("blocked", {}).get(host)
        t = parse_time(until) if until else None
        return t if t and t > utcnow() else None

    def _mark(self, host: str, blocked: bool, reason: str = "") -> None:
        st = self.repo.provider_status().get(NAME, {})
        blocked_map = dict(st.get("blocked", {}))
        reasons = dict(st.get("block_reasons", {}))
        if blocked:
            blocked_map[host] = (utcnow() + timedelta(hours=24)).isoformat()
            reasons[host] = reason
        else:
            blocked_map.pop(host, None)
            reasons.pop(host, None)
        self.repo.update_provider_status(NAME, blocked=blocked_map, block_reasons=reasons)

    async def _allowed(self, url: str) -> bool:
        parts = urlparse(url)
        host = parts.netloc
        if host not in self._robots:
            rp = RobotFileParser()
            try:
                resp = await self.client.get(f"{parts.scheme}://{host}/robots.txt", headers={"User-Agent": USER_AGENT})
                self.repo.count_provider_request(NAME)
                if resp.status_code in (401, 403):
                    rp.disallow_all = True
                elif resp.status_code >= 400:
                    rp.allow_all = True
                else:
                    rp.parse(resp.text.splitlines())
            except httpx.HTTPError:
                rp = None  # robots.txt unreachable: treat the host as unavailable
            self._robots[host] = rp
        rp = self._robots[host]
        return bool(rp and rp.can_fetch(USER_AGENT, url))

    async def get_json(self, url: str, params: dict):
        host = urlparse(url).netloc
        if self._blocked_until(host):
            raise ScrapeBlocked(f"{host} is paused after blocking us (retry after 24h)")
        if not await self._allowed(url):
            raise ScrapeBlocked(f"robots.txt of {host} does not allow this page")
        wait = self.min_interval - (time.monotonic() - self._last.get(host, 0))
        if wait > 0:
            await asyncio.sleep(wait)
        self._last[host] = time.monotonic()
        resp = await self.client.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.repo.count_provider_request(NAME)
        if resp.status_code in BLOCK_CODES:
            self._mark(host, True, f"HTTP {resp.status_code}")
            raise ScrapeBlocked(f"{host} refused automated access (HTTP {resp.status_code}); not retrying for 24h")
        if resp.status_code >= 400:
            raise ScrapeBlocked(f"{host}: HTTP {resp.status_code}")
        self._mark(host, False)
        try:
            return resp.json()
        except ValueError:
            raise ScrapeBlocked(f"{host}: response was not JSON") from None


class ScraperProvider:
    name = NAME
    label = "Web scraping (fallback)"
    method = "scraping"

    def __init__(self, repo: Repository, registry: Registry, settings: dict):
        self.repo, self.registry, self.settings = repo, registry, settings

    def configured(self) -> bool:
        return self.settings.get("scraper_mode", "fallback") != "off"

    async def refresh(self, client: httpx.AsyncClient, competitions: list[dict], now: datetime) -> dict:
        summary = {"provider": NAME, "method": "scraping", "fixtures": 0, "quotes": 0, "errors": [], "skipped": []}
        fetcher = PoliteFetcher(self.repo, client, float(self.settings.get("scraper_min_interval_seconds", 5)))
        known = upcoming(self.repo, now, int(self.settings["lookahead_hours"]))
        sports = sorted({c["sport"] for c in competitions} & set(KAMBI_SPORTS))
        market = self.settings.get("scraper_kambi_market", "DK")
        for op in self.settings.get("scraper_kambi_operators") or []:
            bookmaker = KAMBI_BRANDS.get(op, self.registry.from_name(op))
            for sport in sports:
                url = (f"https://eu-offering-api.kambicdn.com/offering/v2018/{op}/listView/"
                       f"{KAMBI_SPORTS[sport]}/all/all/all/matches.json")
                try:
                    data = await fetcher.get_json(url, {"lang": "en_GB", "market": market, "useCombined": "true"})
                except ScrapeBlocked as exc:
                    summary["errors"].append(str(exc))
                    break  # do not keep knocking on a host that refused us
                except httpx.HTTPError as exc:
                    summary["errors"].append(f"{op}: network error {type(exc).__name__}")
                    break
                for ev in (data or {}).get("events", []) or []:
                    try:
                        n = self._kambi_event(ev, sport, bookmaker, known, now)
                    except Exception as exc:  # malformed event
                        log.debug("skip kambi event: %s", exc)
                        continue
                    if n:
                        summary["fixtures"] += 1
                        summary["quotes"] += n
        fields = {"last_ok": now.isoformat()} if summary["quotes"] else {}
        if summary["errors"]:
            fields.update(last_error=summary["errors"][-1], last_error_at=now.isoformat())
        if fields:
            self.repo.update_provider_status(NAME, **fields)
        return summary

    def _kambi_event(self, ev: dict, sport: str, bookmaker: str, known: list, now: datetime) -> int:
        e = ev.get("event") or {}
        if e.get("state") not in (None, "NOT_STARTED"):
            return 0
        ko = parse_time(e.get("start"))
        home, away = e.get("homeName"), e.get("awayName")
        if not (ko and home and away) or ko <= now:
            return 0
        path = [str(p.get("englishName") or p.get("name") or "") for p in (e.get("path") or [])]
        country = path[1] if len(path) >= 3 else ""  # [sport, country, league]
        leagues = tuple(x for x in (path[-1] if path else "", str(e.get("group", ""))) if x)
        # the whole path goes along as context: a women's marker anywhere in it ("Bundesliga (W)", a "Women"
        # level above the league) keeps the event away from the men's fixtures and competitions
        hit = attach(self.repo, self.registry, NAME, sport, str(e.get("id")), home, away, ko, leagues, known, create=True,
                     country=country, context=tuple(path))
        if not hit:
            return 0
        fx, swapped = hit
        items = []
        for bo in ev.get("betOffers", []) or []:
            kind = ((bo.get("betOfferType") or {}).get("englishName") or (bo.get("betOfferType") or {}).get("name") or "").lower()
            label = ((bo.get("criterion") or {}).get("englishLabel") or (bo.get("criterion") or {}).get("label") or "").lower()
            outs = {o.get("type"): o for o in bo.get("outcomes", []) or [] if o.get("status", "OPEN") == "OPEN"}
            odd = lambda t: (outs[t]["odds"] / 1000) if t in outs and outs[t].get("odds") else None  # noqa: E731
            if kind == "match" and ("full time" in label or "regular" in label or not label):
                if "OT_CROSS" in outs:
                    market = "h2h" if has_draw(sport) else "h2h3"
                    items += [(market, None, "home", odd("OT_ONE")), (market, None, "draw", odd("OT_CROSS")),
                              (market, None, "away", odd("OT_TWO"))]
                else:
                    market = "dnb" if has_draw(sport) else "h2h"
                    items += [(market, None, "home", odd("OT_ONE")), (market, None, "away", odd("OT_TWO"))]
            elif kind == "over/under" and ("total" in label or not label):
                o = outs.get("OT_OVER") or {}
                line = normalise_line(o.get("line", 0) / 1000) if o.get("line") else None
                if line is not None:
                    items += [("totals", line, "over", odd("OT_OVER")), ("totals", line, "under", odd("OT_UNDER"))]
        items = [x for x in items if x[3] and x[3] > 1.0]
        quotes = make_quotes(fx, swapped, items, bookmaker, NAME, now)
        self.repo.upsert_quotes(fx.id, quotes)  # several scraped brands can share a fixture: never delete others
        return len(quotes)
