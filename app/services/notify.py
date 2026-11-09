"""Phone notifications via Telegram and/or ntfy (both free).

* Telegram: create a bot with @BotFather, paste its token, send the bot a
  message, then paste your chat id.
* ntfy: install the ntfy app (iPhone/Android), subscribe to a hard-to-guess
  topic name and enter the same topic here.

After every refresh new alerts are sent once (deduplicated):
pricing errors, strong value bets, arbitrage and new Daily Picks coupons.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

import httpx

from app.core.models import utcnow
from app.i18n import lang_of
from app.services.repository import Repository

log = logging.getLogger("betfinder.notify")

TEXT = {
    "title": ("BetFinder alerts", "BetFinder-alarmer"),
    "error": ("⚠ Possible pricing error", "⚠ Mulig fejlprissætning"),
    "value": ("✅ Value bet", "✅ Værdispil"),
    "arb": ("♻ Arbitrage", "♻ Arbitrage"),
    "pick": ("🎯 Daily pick", "🎯 Dagens kupon"),
    "test": ("BetFinder test message: notifications work.", "BetFinder testbesked: notifikationer virker."),
}


def _t(lang: str, key: str) -> str:
    en, da = TEXT[key]
    return da if lang == "da" else en


def enabled(settings: dict) -> bool:
    return bool((settings.get("notify_telegram_token") and settings.get("notify_telegram_chat_id"))
                or settings.get("notify_ntfy_topic"))


async def send(client: httpx.AsyncClient, settings: dict, title: str, text: str) -> list[str]:
    """Returns a list of channel results ("telegram: ok", "ntfy: HTTP 403" ...)."""
    results = []
    token, chat = settings.get("notify_telegram_token"), settings.get("notify_telegram_chat_id")
    if token and chat:
        try:
            r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  json={"chat_id": chat, "text": f"{title}\n\n{text}"[:4000], "disable_web_page_preview": True})
            results.append("telegram: ok" if r.status_code == 200 else f"telegram: HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            results.append(f"telegram: {type(exc).__name__}")
    topic = settings.get("notify_ntfy_topic")
    if topic:
        server = (settings.get("notify_ntfy_server") or "https://ntfy.sh").rstrip("/")
        try:
            r = await client.post(f"{server}/{topic}", content=text[:4000].encode("utf-8"),
                                  headers={"Title": title.encode("ascii", "ignore").decode() or "BetFinder", "Tags": "moneybag"})
            results.append("ntfy: ok" if r.status_code in (200, 201) else f"ntfy: HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            results.append(f"ntfy: {type(exc).__name__}")
    return results


def collect_alerts(settings: dict, analyses: list[dict], picks: Optional[dict]) -> list[tuple[str, str]]:
    """(dedupe key, line) for every alert-worthy item."""
    lang = lang_of(settings)
    out = []
    min_ev = float(settings.get("notify_min_ev_pct", 6.0))
    for a in analyses:
        fx = a["fixture"]
        name = f"{fx['home']} vs {fx['away']}"
        for s in a["selections"]:
            base = f"{fx['id']}:{s['market_id']}:{s['selection']}:{s['best']['bookmaker']}:{s['best']['odds']}"
            if settings.get("notify_on_errors") and s.get("deviation_level") == "alert" and s.get("pricing_flags"):
                out.append((f"err:{base}", f"{_t(lang, 'error')}: {name} – {s['label']} @ {s['best']['odds']:.2f} "
                                           f"({s['best']['name']}), {s['market_ev_pct']:+.1f}% vs fair"))
            elif settings.get("notify_on_value") and s.get("qualifies") and (s.get("ev_pct") or 0) >= min_ev:
                out.append((f"val:{base}", f"{_t(lang, 'value')}: {name} – {s['label']} @ {s['best']['odds']:.2f} "
                                           f"({s['best']['name']}), EV {s['ev_pct']:+.1f}%"))
        if settings.get("notify_on_arbitrage"):
            for m in a["markets"]:
                if m.get("arbitrage_pct"):
                    out.append((f"arb:{fx['id']}:{m['market_id']}:{m['arbitrage_pct']}",
                                f"{_t(lang, 'arb')}: {name} – {m['label']} +{m['arbitrage_pct']:.1f}%"))
    if picks and settings.get("notify_on_picks"):
        for c in picks.get("coupons", []):
            legs =", ".join(f"{leg['selection_label']} @ {leg['odds']:.2f}" for leg in c["legs"])
            out.append((f"pick:{picks['date']}:{c['id']}", f"{_t(lang, 'pick')} ({c['combined_odds']:.2f}): {legs}"))
    return out


async def process(client: httpx.AsyncClient, repo: Repository, settings: dict, analyses: list[dict],
                  picks: Optional[dict]) -> dict:
    if not enabled(settings):
        return {"sent": 0, "channels": [], "skipped": "not_configured"}
    alerts = collect_alerts(settings, analyses, picks)
    now = utcnow()
    with repo.store.edit("notifications_log", {}) as log_data:
        cutoff = (now - timedelta(days=7)).isoformat()
        for k in [k for k, v in log_data.items() if v < cutoff]:
            del log_data[k]
        new = [(k, line) for k, line in alerts if k not in log_data]
        if not new:
            return {"sent": 0, "channels": []}
        for k, _ in new:
            log_data[k] = now.isoformat()
    lang = lang_of(settings)
    text = "\n".join(line for _, line in new[:12])
    if len(new) > 12:
        text += f"\n… +{len(new) - 12}"
    channels = await send(client, settings, _t(lang, "title"), text)
    return {"sent": len(new), "channels": channels}


async def test(settings: dict) -> list[str]:
    async with httpx.AsyncClient(timeout=15) as client:
        return await send(client, settings, "BetFinder", _t(lang_of(settings), "test"))
