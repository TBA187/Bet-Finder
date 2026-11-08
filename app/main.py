"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import APP_VERSION, BASE_DIR, Paths, SettingsError, SettingsService
from app.core.odds_math import OddsError
from app.services.analysis import AnalysisService
from app.services.registry import Registry
from app.services.repository import Repository
from app.services.refresh import RefreshService
from app.services.tracker import TrackerError, TrackerService
from app.storage.json_store import JsonStore

log = logging.getLogger("betfinder")


class Container:
    """Holds the long-lived services; one per app instance."""

    def __init__(self, paths: Paths):
        self.paths = paths
        self.store = JsonStore(paths.runtime)
        self.repo = Repository(self.store)
        self.registry = Registry(paths.registry, self.store)
        self.settings = SettingsService(self.store)
        self.analysis = AnalysisService(self.repo, self.registry)
        self.tracker = TrackerService(self.repo, self.registry)
        self.refresh = RefreshService(self.repo, self.registry, self.settings, self.tracker)
        self.token = os.environ.get("BETFINDER_TOKEN", "").strip()


async def _auto_refresh(container: Container) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            minutes = int(container.settings.get()["auto_refresh_minutes"])
            if minutes <= 0:
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
        task = asyncio.create_task(_auto_refresh(container)) if background else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="BetFinder", version=APP_VERSION, lifespan=lifespan,
                  description="European bookmaker odds scanner and value-bet analysis with 10 specialised agents.")
    app.state.container = container

    @app.middleware("http")
    async def token_guard(request: Request, call_next):
        if container.token and request.url.path.startswith("/api/") and request.url.path != "/api/health":
            if not hmac.compare_digest(request.headers.get("x-betfinder-token", ""), container.token):
                return JSONResponse({"detail": "Access token required"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(SettingsError)
    @app.exception_handler(TrackerError)
    @app.exception_handler(OddsError)
    async def bad_request(_request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"version": APP_VERSION})

    from app.api.routes import build_router
    app.include_router(build_router(container), prefix="/api")
    return app
