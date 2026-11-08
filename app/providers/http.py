"""HTTP helper for data providers: response caching, retries, quota tracking
and API-key redaction (keys never appear in errors, logs or the UI)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.services.repository import Repository

log = logging.getLogger("betfinder.http")


class ProviderError(Exception):
    """A provider call failed.  The message is safe to show to the user."""


class Budget:
    """Caps the number of real (non-cached) requests in one refresh."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self.skipped: list[str] = []

    def take(self, what: str) -> bool:
        if self.used >= self.limit:
            self.skipped.append(what)
            return False
        self.used += 1
        return True


class CachedClient:
    def __init__(self, repo: Repository, provider: str, client: httpx.AsyncClient, secrets: tuple[str, ...] = ()):
        self.repo = repo
        self.provider = provider
        self.client = client
        self.secrets = tuple(s for s in secrets if s)
        self.last_headers: dict[str, str] = {}

    def _redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return text

    @staticmethod
    def _cache_name(provider: str, url: str, params: dict) -> str:
        safe = {k: v for k, v in sorted(params.items()) if k.lower() not in ("apikey", "api_key", "key")}
        digest = hashlib.sha1(f"{url}?{json.dumps(safe, sort_keys=True, default=str)}".encode()).hexdigest()[:20]
        return f"cache/{provider}/{digest}"

    def cached(self, url: str, params: dict, ttl_seconds: int) -> Optional[Any]:
        name = self._cache_name(self.provider, url, params)
        entry = self.repo.store.read(name, None)
        if not entry:
            return None
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(entry["fetched_at"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            return None
        return entry.get("data") if age <= ttl_seconds else None

    async def get_json(self, url: str, *, params: dict, headers: Optional[dict] = None, ttl_seconds: int = 0,
                       budget: Optional[Budget] = None, what: str = "") -> Any:
        if ttl_seconds > 0:
            hit = self.cached(url, params, ttl_seconds)
            if hit is not None:
                return hit
        if budget is not None and not budget.take(what or url):
            raise ProviderError(f"Request budget for this refresh used up (skipped: {what or url})")
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = await self.client.get(url, params=params, headers=headers or {})
            except httpx.HTTPError as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            self.last_headers = {k.lower(): v for k, v in resp.headers.items()}
            self.repo.count_provider_request(self.provider)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code == 401 or resp.status_code == 403:
                raise ProviderError(f"{self.provider}: API key rejected (HTTP {resp.status_code})")
            if resp.status_code == 422:
                raise ProviderError(f"{self.provider}: request rejected (HTTP 422) - {self._redact(resp.text[:200])}")
            if resp.status_code >= 400:
                raise ProviderError(f"{self.provider}: HTTP {resp.status_code} - {self._redact(resp.text[:200])}")
            try:
                data = resp.json()
            except ValueError:
                raise ProviderError(f"{self.provider}: response was not JSON") from None
            if ttl_seconds > 0:
                self.repo.store.write(self._cache_name(self.provider, url, params),
                                      {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data}, pretty=False)
            return data
        raise ProviderError(f"{self.provider}: network error ({type(last_exc).__name__})")
