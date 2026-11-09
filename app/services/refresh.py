"""Coordinates a data refresh across all sources, with a fallback chain:

1. The Odds API (API credits)                 - main odds feed
2. API-Sports (API requests)                  - fixtures, stats, injuries, odds
3. odds-api.io (API requests, paid)           - Danske Spil / Eurobet supplement
4. OddsPapi (free monthly requests)           - used when 1 has no key or no credits left
5. Polite scraper                             - used when 1-4 deliver no odds (or always, if chosen)

Every refresh records which method actually delivered odds (API or scraping),
so the UI can show it.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable, Optional

import httpx

from app.config import APP_VERSION, SettingsService
from app.core.models import utcnow
from app.i18n import lang_of, tr
from app.providers.api_sports import ApiSportsProvider
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.oddspapi import OddsPapiProvider
from app.providers.scraper import ScraperProvider
from app.providers.the_odds_api import TheOddsApiProvider
from app.services.registry import Registry
from app.services.repository import Repository
from app.services.tracker import TrackerService

log = logging.getLogger("betfinder.refresh")


class RefreshService:
    def __init__(self, repo: Repository, registry: Registry, settings: SettingsService, tracker: TrackerService):
        self.repo, self.registry, self.settings, self.tracker = repo, registry, settings, tracker
        self._lock = asyncio.Lock()
        self.running = False
        self.after: Optional[Callable[[httpx.AsyncClient, dict, datetime], Awaitable[dict]]] = None

    def providers(self, settings: dict) -> list:
        return [TheOddsApiProvider(self.repo, self.registry, settings), ApiSportsProvider(self.repo, self.registry, settings),
                OddsApiIoProvider(self.repo, self.registry, settings), OddsPapiProvider(self.repo, self.registry, settings),
                ScraperProvider(self.repo, self.registry, settings)]

    def last(self) -> Optional[dict]:
        return self.repo.provider_status().get("_last_refresh")

    async def run(self, now: Optional[datetime] = None) -> dict:
        if self._lock.locked():
            return {"status": "busy", "message": "A refresh is already running"}
        async with self._lock:
            self.running = True
            try:
                return await self._run(now or utcnow())
            finally:
                self.running = False

    @staticmethod
    async def _safe(provider, client, competitions, now) -> dict:
        try:
            return await provider.refresh(client, competitions, now)
        except Exception as exc:  # never let one provider break the refresh
            log.exception("Provider %s failed", provider.name)
            return {"provider": provider.name, "quotes": 0, "fixtures": 0, "errors": [f"internal error: {type(exc).__name__}"]}

    async def _run(self, now: datetime) -> dict:
        settings = self.settings.get()
        competitions = self.registry.competitions(enabled_only=True)
        odds_api, api_sports, odds_io, oddspapi, scraper = self.providers(settings)
        summaries: list[dict] = []
        chain: list[dict] = []
        after_result = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0),
                                     headers={"User-Agent": f"BetFinder/{APP_VERSION}"}) as client:
            primary_ok = False
            primary = [p for p in (odds_api, api_sports, odds_io) if p.configured()]
            for prov in (odds_api, api_sports, odds_io):
                if not prov.configured():
                    chain.append({"provider": prov.name, "method": "api", "ran": False, "reason": "not_configured"})
            # the API sources are independent: run them at the same time
            results = await asyncio.gather(*(self._safe(p, client, competitions, now) for p in primary))
            for prov, s in zip(primary, results):
                summaries.append(s)
                failed = bool(s.get("errors")) and not s.get("quotes")
                chain.append({"provider": prov.name, "method": "api", "ran": True, "quotes": s.get("quotes", 0),
                              "reason": "exhausted" if s.get("exhausted") else "error" if failed else "ok"})
                if prov is odds_api and not s.get("exhausted") and not any("rejected" in e for e in s.get("errors", [])):
                    primary_ok = True
                if prov is api_sports and s.get("quotes", 0) > 0:
                    primary_ok = True
            # --- fallback 1: OddsPapi free budget
            if not primary_ok and oddspapi.configured():
                s = await self._safe(oddspapi, client, competitions, now)
                summaries.append(s)
                chain.append({"provider": oddspapi.name, "method": "api", "ran": True, "quotes": s.get("quotes", 0),
                              "reason": "exhausted" if s.get("exhausted") else "fallback"})
                primary_ok = s.get("quotes", 0) > 0 or (not s.get("exhausted") and not s.get("errors"))
            else:
                chain.append({"provider": oddspapi.name, "method": "api", "ran": False,
                              "reason": "not_needed" if primary_ok else "not_configured"})
            # --- fallback 2: polite scraping
            mode = settings.get("scraper_mode", "fallback")
            if mode == "always" or (mode == "fallback" and not primary_ok):
                s = await self._safe(scraper, client, competitions, now)
                summaries.append(s)
                chain.append({"provider": scraper.name, "method": "scraping", "ran": True, "quotes": s.get("quotes", 0),
                              "reason": "always" if mode == "always" else "fallback"})
            else:
                chain.append({"provider": scraper.name, "method": "scraping", "ran": False,
                              "reason": "off" if mode == "off" else "not_needed"})
            pruned = self.repo.prune(int(settings["retention_days"]), keep=self.tracker.referenced_fixtures())
            self.tracker.refresh(settings)
            if self.after:
                try:
                    after_result = await self.after(client, settings, now)
                except Exception:
                    log.exception("post-refresh hook failed")
        methods = sorted({c["method"] for c in chain if c.get("ran") and c.get("quotes")})
        delivered = any(c.get("ran") for c in chain)
        result = {
            "status": "ok" if all(not s.get("errors") for s in summaries) else "partial",
            "at": now.isoformat(),
            "providers": summaries,
            "chain": chain,
            "odds_methods": methods,
            "pruned_fixtures": pruned,
            "notifications": after_result,
            "message": None if methods else tr(lang_of(settings), "refresh_no_source"),
        }
        self.repo.update_provider_status("_last_refresh", **result)
        return result

    async def fetch_more(self, fixture, now: Optional[datetime] = None) -> dict:
        """On-demand extra bookmakers for one match (OddsPapi free budget)."""
        settings = self.settings.get()
        prov = OddsPapiProvider(self.repo, self.registry, settings)
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0),
                                     headers={"User-Agent": f"BetFinder/{APP_VERSION}"}) as client:
            return await prov.fetch_fixture(client, fixture, now or utcnow())
