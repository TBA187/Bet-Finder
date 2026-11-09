"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import APP_VERSION, BASE_DIR, Paths, SettingsError, SettingsService, tz
from app.core.models import Fixture, utcnow
from app.core.odds_math import OddsError
from app.services import notify
from app.services.analysis import AnalysisService
from app.services.daily_picks import build_daily_picks
from app.services.registry import Registry
from app.services.repository import Repository
from app.services.refresh import RefreshService
from app.services.tracker import TrackerError, TrackerService
from app.storage.json_store import make_store

log = logging.getLogger("betfinder")


class Container:
    """Holds the long-lived services; one per app instance."""

    def __init__(self, paths: Paths):
        self.paths = paths
        self.store = make_store(paths.runtime)
        self.repo = Repository(self.store)
        self.registry = Registry(paths.registry, self.store)
        self.settings = SettingsService(self.store)
        self.analysis = AnalysisService(self.repo, self.registry)
        self.tracker = TrackerService(self.repo, self.registry)
        self.refresh = RefreshService(self.repo, self.registry, self.settings, self.tracker)
        self.refresh.after = self._after_refresh
        self.token = os.environ.get("BETFINDER_TOKEN", "").strip()
        try:  # BetFinder uses live data only: remove anything left from the old demo mode
            self.repo.purge_demo()
        except Exception:
            log.exception("could not purge old demo data")

    # shared views used by several endpoints and by the scheduler -----------
    def upcoming(self, now: datetime, s: dict) -> list[Fixture]:
        horizon = now + timedelta(hours=int(s["lookahead_hours"]))
        return sorted((fx for fx in self.repo.fixtures(include_demo=False)
                       if fx.status == "scheduled" and now < fx.kickoff <= horizon), key=lambda f: f.kickoff)

    def analyses(self, now: datetime, s: dict) -> list[dict]:
        return [self.analysis.analyze(fx, s, now) for fx in self.upcoming(now, s)]

    def daily_picks(self, now: datetime, s: dict, analyses: Optional[list[dict]] = None) -> dict:
        result = build_daily_picks(analyses if analyses is not None else self.analyses(now, s), s, now)
        for coupon in result["coupons"]:
            key = json.dumps([(leg["fixture_id"], leg["market_id"], leg["selection"], leg["bookmaker"], leg["odds"])
                              for leg in coupon["legs"]])
            coupon["id"] = hashlib.sha1(key.encode()).hexdigest()[:12]
        with self.store.edit("daily_picks", {}) as data:
            day = data.setdefault(result["date"], {"coupons": {}, "latest": None})
            for coupon in result["coupons"]:
                day["coupons"][coupon["id"]] = coupon
            day["latest"] = {k: v for k, v in result.items() if k != "singles"}
            for old in sorted(data)[:-30]:
                data.pop(old, None)
        return result

    async def _after_refresh(self, client, settings: dict, now: datetime) -> dict:
        if not notify.enabled(settings):
            return {"sent": 0, "skipped": "not_configured"}
        analyses = self.analyses(now, settings)
        picks = self.daily_picks(now, settings, analyses)
        return await notify.process(client, self.repo, settings, analyses, picks)


def _in_active_hours(settings: dict) -> bool:
    hour = datetime.now(timezone.utc).astimezone(tz(settings)).hour
    start, end = int(settings["active_hours_start"]), int(settings["active_hours_end"])
    return start <= hour < end if start < end else (hour >= start or hour < end)


async def _auto_refresh(container: Container) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            settings = container.settings.get()
            minutes = int(settings["auto_refresh_minutes"])
            if minutes <= 0 or not _in_active_hours(settings):
                continue
            last = container.refresh.last()
            last_at = datetime.fromisoformat(last["at"]) if last and last.get("at") else None
            if last_at is None or (datetime.now(timezone.utc) - last_at).total_seconds() >= minutes * 60:
                await container.refresh.run()
        except Exception:
            log.exception("Automatic refresh failed")


def create_app(paths: Optional[Paths] = None, *, background: bool = True) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    container = Container(paths or Paths.default())

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        # On serverless hosts (Vercel) nothing runs between requests: use /api/cron/refresh instead.
        task = asyncio.create_task(_auto_refresh(container)) if background and not os.environ.get("VERCEL") else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="BetFinder", version=APP_VERSION, lifespan=lifespan,
                  description="European bookmaker odds scanner and value-bet analysis with 10 specialised agents.")
    app.state.container = container

    @app.middleware("http")
    async def token_guard(request: Request, call_next):
        path = request.url.path
        if container.token and path.startswith("/api/") and path != "/api/health" and not path.startswith("/api/cron/"):
            if not hmac.compare_digest(request.headers.get("x-betfinder-token", ""), container.token):
                return JSONResponse({"detail": "Access token required"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(SettingsError)
    @app.exception_handler(TrackerError)
    @app.exception_handler(OddsError)
    async def bad_request(_request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    static_dir = BASE_DIR / "app" / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"version": APP_VERSION})

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker():
        # served from the root so it can control the whole app (installable PWA)
        return FileResponse(static_dir / "sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    from app.api.routes import build_router
    app.include_router(build_router(container), prefix="/api")
    return app
