"""Application paths and user settings (stored in data/runtime/settings.json).

API keys can come from environment variables (preferred on a server) or be
entered in the UI, in which case they are stored in the git-ignored runtime
folder.  They are never returned in full by the API.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.odds_math import DEVIG_METHODS
from app.storage.json_store import JsonStore

APP_VERSION = "1.3.0"
BASE_DIR = Path(__file__).resolve().parent.parent

RISK_LEVELS = ("low", "medium", "high", "very_high")


@dataclass(frozen=True)
class Paths:
    registry: Path
    runtime: Path

    @classmethod
    def default(cls) -> "Paths":
        data = Path(os.environ.get("BETFINDER_DATA_DIR", BASE_DIR / "data"))
        return cls(registry=BASE_DIR / "data" / "registry", runtime=data / "runtime")


# key -> (default, validator spec)
_SPEC: dict[str, tuple[Any, dict]] = {
    "language": ("en", {"type": str, "choices": ("en", "da")}),
    "currency": ("DKK", {"type": str, "choices": ("DKK", "EUR", "SEK", "NOK", "GBP", "USD")}),
    "timezone": ("Europe/Copenhagen", {"type": "tz"}),
    "day_end_hour": (6, {"type": int, "min": 0, "max": 12}),
    "lookahead_hours": (72, {"type": int, "min": 6, "max": 168}),
    "devig_method": ("shin", {"type": str, "choices": DEVIG_METHODS}),
    # Value / qualification thresholds
    "min_ev_pct": (3.0, {"type": float, "min": 0.0, "max": 50.0}),
    # The price must be at least this far above the margin-free market price.
    # 0 = the agents can raise or lower EV but can never create value alone
    # against an efficient market.  Set to -100 to disable.
    "min_market_edge_pct": (0.0, {"type": float, "min": -100.0, "max": 50.0}),
    "min_confidence": (55, {"type": int, "min": 0, "max": 100}),
    "max_risk": ("medium", {"type": str, "choices": RISK_LEVELS}),
    "min_leg_probability": (0.25, {"type": float, "min": 0.01, "max": 0.95}),
    "max_leg_odds": (6.0, {"type": float, "min": 1.1, "max": 100.0}),
    "suspicious_ev_pct": (20.0, {"type": float, "min": 5.0, "max": 200.0}),
    "stale_minutes": (240, {"type": int, "min": 10, "max": 10080}),
    # Daily picks
    "coupon_min_odds": (3.0, {"type": float, "min": 1.5, "max": 50.0}),
    "coupon_max_legs": (3, {"type": int, "min": 1, "max": 3}),
    "max_coupons": (3, {"type": int, "min": 1, "max": 5}),
    # Price deviation traffic light (price vs margin-free fair odds)
    "deviation_warning_pct": (5.0, {"type": float, "min": 1.0, "max": 50.0}),
    "deviation_alert_pct": (10.0, {"type": float, "min": 2.0, "max": 100.0}),
    # Model blending
    "model_weight": (0.30, {"type": float, "min": 0.0, "max": 0.6}),
    "max_logit_shift": (0.30, {"type": float, "min": 0.0, "max": 1.0}),
    # Staking
    "bankroll": (1000.0, {"type": float, "min": 0.0, "max": 1e9}),
    "kelly_fraction": (0.25, {"type": float, "min": 0.0, "max": 1.0}),
    # Bookmakers the user can actually bet with (empty = all)
    "my_bookmakers": ([], {"type": list}),
    # Providers
    "odds_api_key": ("", {"type": str, "secret": True}),
    "odds_api_regions": (["eu"], {"type": list, "choices": ("eu", "uk", "se", "fr", "us", "us2", "au")}),
    "odds_api_markets": (["h2h", "totals"], {"type": list, "choices": ("h2h", "totals", "spreads")}),
    "api_sports_key": ("", {"type": str, "secret": True}),
    "api_sports_max_requests_per_refresh": (60, {"type": int, "min": 1, "max": 5000}),
    "api_sports_daily_reserve": (5, {"type": int, "min": 0, "max": 1000}),
    "api_sports_fetch_xg": (True, {"type": bool}),
    # odds-api.io (paid): the only feed found that carries Danske Spil and Eurobet
    "odds_api_io_key": ("", {"type": str, "secret": True}),
    "odds_api_io_bookmakers": (["DanskeSpil", "Eurobet IT"], {"type": list}),
    # OddsPapi (free 250 requests/month): extra bookmakers on demand
    "oddspapi_key": ("", {"type": str, "secret": True}),
    "oddspapi_bookmakers": (["bet365", "pinnacle", "unibet", "williamhill", "betway", "1xbet", "eurobet"], {"type": list}),
    "oddspapi_monthly_budget": (240, {"type": int, "min": 0, "max": 100000}),
    # Scraping fallback (polite: robots.txt, rate limited, never bypasses blocking)
    "scraper_mode": ("fallback", {"type": str, "choices": ("off", "fallback", "always")}),
    "scraper_kambi_operators": (["ub"], {"type": list}),
    "scraper_kambi_market": ("DK", {"type": str}),
    "scraper_min_interval_seconds": (2, {"type": int, "min": 1, "max": 600}),
    # Scheduler
    "auto_refresh_minutes": (0, {"type": int, "min": 0, "max": 1440}),
    "active_hours_start": (8, {"type": int, "min": 0, "max": 23}),
    "active_hours_end": (24, {"type": int, "min": 1, "max": 24}),
    # Notifications (Telegram / ntfy)
    "notify_telegram_token": ("", {"type": str, "secret": True}),
    "notify_telegram_chat_id": ("", {"type": str}),
    "notify_ntfy_topic": ("", {"type": str}),
    "notify_ntfy_server": ("https://ntfy.sh", {"type": str, "url": True}),
    "notify_on_errors": (True, {"type": bool}),
    "notify_on_value": (True, {"type": bool}),
    "notify_on_picks": (True, {"type": bool}),
    "notify_on_arbitrage": (True, {"type": bool}),
    "notify_min_ev_pct": (6.0, {"type": float, "min": 0.0, "max": 100.0}),
    "retention_days": (21, {"type": int, "min": 2, "max": 365}),
}

SECRET_ENV = {"odds_api_key": "ODDS_API_KEY", "api_sports_key": "API_SPORTS_KEY", "odds_api_io_key": "ODDS_API_IO_KEY",
              "oddspapi_key": "ODDSPAPI_KEY", "notify_telegram_token": "TELEGRAM_BOT_TOKEN"}

DEFAULT_SETTINGS = {k: v[0] for k, v in _SPEC.items()}


class SettingsError(ValueError):
    pass


def _coerce(key: str, value: Any) -> Any:
    default, spec = _SPEC[key]
    kind = spec["type"]
    if kind == "tz":
        try:
            ZoneInfo(str(value))
        except (ZoneInfoNotFoundError, ValueError):
            raise SettingsError(f"Unknown timezone {value!r}") from None
        return str(value)
    if kind is bool:
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return bool(value)
    if kind is list:
        if not isinstance(value, list):
            raise SettingsError(f"{key} must be a list")
        items = [str(v) for v in value]
        choices = spec.get("choices")
        if choices:
            bad = [v for v in items if v not in choices]
            if bad:
                raise SettingsError(f"{key}: invalid values {bad}; allowed {list(choices)}")
            if not items:
                raise SettingsError(f"{key} needs at least one value")
        return list(dict.fromkeys(items))
    try:
        coerced = kind(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{key} must be of type {kind.__name__}") from None
    if "choices" in spec and coerced not in spec["choices"]:
        raise SettingsError(f"{key} must be one of {list(spec['choices'])}")
    if "min" in spec and coerced < spec["min"]:
        raise SettingsError(f"{key} must be >= {spec['min']}")
    if "max" in spec and coerced > spec["max"]:
        raise SettingsError(f"{key} must be <= {spec['max']}")
    if kind is str:
        coerced = coerced.strip()
        if spec.get("url") and coerced and not coerced.startswith(("https://", "http://")):
            raise SettingsError(f"{key} must start with https://")
        if len(coerced) > 300:
            raise SettingsError(f"{key} is too long")
    return coerced


class SettingsService:
    FILE = "settings"

    def __init__(self, store: JsonStore):
        self.store = store

    def _stored(self) -> dict:
        data = self.store.read(self.FILE, {})
        return data if isinstance(data, dict) else {}

    def get(self) -> dict:
        merged = dict(DEFAULT_SETTINGS)
        for key, value in self._stored().items():
            if key in _SPEC:
                try:
                    merged[key] = _coerce(key, value)
                except SettingsError:
                    pass  # ignore invalid stored values, keep default
        for key, env in SECRET_ENV.items():
            if os.environ.get(env):
                merged[key] = os.environ[env].strip()
        return merged

    def secret_source(self, key: str) -> str:
        if os.environ.get(SECRET_ENV[key]):
            return "environment"
        return "settings" if self._stored().get(key) else "none"

    def public(self) -> dict:
        data = self.get()
        out = {}
        for key, value in data.items():
            if _SPEC[key][1].get("secret"):
                out[key] = {"configured": bool(value), "source": self.secret_source(key),
                            "hint": (f"…{value[-4:]}" if value and len(value) > 8 else "")}
            else:
                out[key] = value
        return out

    def update(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise SettingsError("Settings patch must be an object")
        clean = {}
        for key, value in patch.items():
            if key not in _SPEC:
                raise SettingsError(f"Unknown setting {key!r}")
            clean[key] = _coerce(key, value)
        with self.store.edit(self.FILE, {}) as stored:
            if not isinstance(stored, dict):
                raise SettingsError("settings.json is not a JSON object - delete it to reset")
            stored.update(clean)
        return self.public()


def tz(settings: dict) -> ZoneInfo:
    try:
        return ZoneInfo(settings.get("timezone") or "Europe/Copenhagen")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
