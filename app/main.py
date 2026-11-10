"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import APP_VERSION, BASE_DIR, Paths, SettingsError, SettingsService, tz
from app.core.models import Fixture
from app.core.odds_math import OddsError
from app.services import notify
from app.services.analysis import AnalysisService
from app.services.auth import CURRENT_USER, AuthError, AuthService, permissions
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
        self.auth = AuthService(self.store)
        try:  # first start: super admin  admin / admin  (BetFinder asks to change it)
            self.auth.ensure_admin()
        except Exception:
            log.exception("could not create the first admin account")

    # shared views used by several endpoints and by the scheduler -----------
    def upcoming(self, now: datetime, s: dict) -> list[Fixture]:
        horizon = now + timedelta(hours=int(s["lookahead_hours"]))
        return sorted((fx for fx in self.repo.fixtures()
                       if fx.status == "scheduled" and now < fx.kickoff <= horizon), key=lambda f: f.kickoff)

    def analyses(self, now: datetime, s: dict) -> list[dict]:
        return [self.analysis.analyze(fx, s, now) for fx in self.upcoming(now, s)]

    def daily_picks(self, now: datetime, s: dict, analyses: Optional[list[dict]] = None, scope: str = "today") -> dict:
        result = build_daily_picks(analyses if analyses is not None else self.analyses(now, s), s, now, scope)
        for coupon in result["coupons"]:
            key = json.dumps([(leg["fixture_id"], leg["market_id"], leg["selection"], leg["bookmaker"], leg["odds"])
                              for leg in coupon["legs"]])
            coupon["id"] = hashlib.sha1(key.encode()).hexdigest()[:12]
        # Coupons are kept by id so "Track coupon" still finds them later.  Only write when
        # something changed: every write also resets the analysis cache for all visitors.
        stored = (self.store.read("daily_picks", {}).get(result["date"]) or {}).get("coupons") or {}
        if any(stored.get(coupon["id"]) != coupon for coupon in result["coupons"]):
            with self.store.edit("daily_picks", {}) as data:
                day = data.setdefault(result["date"], {})
                day.pop("latest", None)  # no longer stored
                day.setdefault("coupons", {}).update({coupon["id"]: coupon for coupon in result["coupons"]})
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


# -------------------------------------------------------------- access rules
PUBLIC_API = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/request-account", "/api/auth/forgot-password"}
ANY = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
RULES = [  # first match wins; anything not listed: reading = "view", changing = "admin"
    (("POST",), re.compile(r"^/api/refresh$"), "refresh"),
    (("POST",), re.compile(r"^/api/fixtures/[^/]+/fetch-more$"), "refresh"),
    (("POST",), re.compile(r"^/api/manual/"), "manual"),
    (("POST", "PUT", "DELETE"), re.compile(r"^/api/fixtures/[^/]+/(quotes|absences|lineups|xg|result)(/|$)"), "manual"),
    (("POST",), re.compile(r"^/api/builder$"), "view"),
    (("POST", "DELETE"), re.compile(r"^/api/tracker(/|$)"), "track"),
    (("GET",), re.compile(r"^/api/(usage|credits-plan)$"), "usage"),
    (("GET",), re.compile(r"^/api/settings$"), "admin"),
    (ANY, re.compile(r"^/api/admin(/|$)"), "admin"),
    (ANY, re.compile(r"^/api/(auth|account)(/|$)"), "view"),
]


def required_permission(method: str, path: str) -> str:
    for methods, pattern, perm in RULES:
        if method in methods and pattern.match(path):
            return perm
    return "view" if method in ("GET", "HEAD") else "admin"


def _same_site(request: Request) -> bool:
    """Blocks other websites from sending changes with a signed-in visitor's cookie."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # not a browser form (curl, scripts); the session cookie is still required
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return urlparse(origin).netloc == host


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
    async def access_guard(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path.startswith("/api/cron/"):
            return await call_next(request)  # pages, static files, scheduler (has its own secret)
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_site(request):
            return JSONResponse({"detail": "Request blocked: it did not come from this site", "code": "cross_site"}, 403)
        if path in PUBLIC_API:
            return await call_next(request)
        token = request.cookies.get("bf_session")
        try:
            user = await run_in_threadpool(container.auth.session_user, token) if token else None
        except Exception:
            log.exception("session check failed")
            return JSONResponse({"detail": "Storage is not reachable right now. Try again.", "code": "storage"}, 503)
        if not user:
            return JSONResponse({"detail": "Please sign in", "code": "auth_required"}, 401)
        if required_permission(request.method, path) not in permissions(user["role"]):
            return JSONResponse({"detail": "Your account does not have access to this", "code": "forbidden"}, 403)
        reset = CURRENT_USER.set(user)
        try:
            return await call_next(request)
        finally:
            CURRENT_USER.reset(reset)

    @app.exception_handler(AuthError)
    async def auth_error(_request: Request, exc: AuthError):
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)

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

    from app.api.auth_routes import build_auth_router
    from app.api.routes import build_router
    app.include_router(build_auth_router(container), prefix="/api")
    app.include_router(build_router(container), prefix="/api")
    return app
