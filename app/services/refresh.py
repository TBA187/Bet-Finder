"""Coordinates a data refresh across all configured providers."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

from app.config import APP_VERSION, SettingsService
from app.core.models import utcnow
from app.providers.api_sports import ApiSportsProvider
from app.providers.demo import load_demo
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

    def providers(self, settings: dict) -> list:
        return [TheOddsApiProvider(self.repo, self.registry, settings), ApiSportsProvider(self.repo, self.registry, settings)]

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

    async def _run(self, now: datetime) -> dict:
        settings = self.settings.get()
        competitions = self.registry.competitions(enabled_only=True)
        summaries = []
        providers = [p for p in self.providers(settings) if p.configured()]
        if providers:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0),
                                         headers={"User-Agent": f"BetFinder/{APP_VERSION}"}) as client:
                for provider in providers:
                    try:
                        summaries.append(await provider.refresh(client, competitions, now))
                    except Exception as exc:  # never let one provider break the refresh
                        log.exception("Provider %s failed", provider.name)
                        summaries.append({"provider": provider.name, "errors": [f"internal error: {type(exc).__name__}"]})
        demo = None
        if settings["demo_mode"]:
            demo = load_demo(self.repo, self.registry, now)
        pruned = self.repo.prune(int(settings["retention_days"]), keep=self.tracker.referenced_fixtures())
        self.tracker.refresh(settings)
        result = {
            "status": "ok" if all(not s.get("errors") for s in summaries) else "partial",
            "at": now.isoformat(),
            "providers": summaries,
            "demo_fixtures": demo,
            "pruned_fixtures": pruned,
            "message": None if providers or demo else
            "No data provider is configured. Add an API key under Sources, enter odds manually, or switch on demo mode.",
        }
        self.repo.update_provider_status("_last_refresh", **result)
        return result
