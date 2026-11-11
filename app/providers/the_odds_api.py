"""The Odds API (https://the-odds-api.com) - live pre-match odds from many
European bookmakers (NordicBet, Betsson, Unibet, Pinnacle, Betfair, ...).

Quota: each /odds call costs (#markets x #regions) credits; /sports is free.
The free plan has 500 credits per month, so BetFinder caches responses and
skips competitions that are out of season.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.core.markets import market_id, normalise_line, swap_sides
from app.core.models import Fixture, Quote
from app.core.names import team_similarity
from app.core.sports import has_draw
from app.providers.http import CachedClient, ProviderError
from app.services.registry import Registry
from app.services.repository import Repository

log = logging.getLogger("betfinder.the_odds_api")
BASE = "https://api.the-odds-api.com/v4"
NAME = "the_odds_api"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class TheOddsApiProvider:
    name = NAME
    label = "The Odds API · the-odds-api.com"

    def __init__(self, repo: Repository, registry: Registry, settings: dict):
        self.repo, self.registry, self.settings = repo, registry, settings
        self.key = settings.get("odds_api_key", "")

    def configured(self) -> bool:
        return bool(self.key)

    async def refresh(self, client: httpx.AsyncClient, competitions: list[dict], now: datetime) -> dict:
        summary = {"provider": NAME, "fixtures": 0, "quotes": 0, "results": 0, "errors": [], "skipped": [], "requests": 0}
        if not self.configured():
            summary["skipped"].append("No API key configured")
            return summary
        cc = CachedClient(self.repo, NAME, client, (self.key,))
        try:
            sports = await cc.get_json(f"{BASE}/sports", params={"apiKey": self.key}, ttl_seconds=6 * 3600)
            active = {s["key"] for s in sports if isinstance(s, dict) and s.get("active")}
        except ProviderError as exc:
            summary["errors"].append(str(exc))
            self.repo.update_provider_status(NAME, last_error=str(exc), last_error_at=now.isoformat())
            return summary
        regions = ",".join(self.settings.get("odds_api_regions") or ["eu"])
        markets = ",".join(self.settings.get("odds_api_markets") or ["h2h"])
        horizon = now + timedelta(hours=int(self.settings.get("lookahead_hours", 48)))
        summary["exhausted"] = False
        jobs = []
        for comp in competitions:
            pattern = comp.get("providers", {}).get("the_odds_api")
            if not pattern:
                continue
            # wildcard keys (e.g. tennis_atp_*) expand to the tournaments that are currently active
            keys = sorted(k for k in active if fnmatch.fnmatch(k, pattern)) if "*" in pattern else [pattern]
            if not keys or (keys == [pattern] and pattern not in active):
                summary["skipped"].append(f"{comp['name']}: not in season at The Odds API")
                continue
            jobs += [(comp, k) for k in keys]
        window = {"commenceTimeFrom": _iso(now), "commenceTimeTo": _iso(horizon)}
        sem = asyncio.Semaphore(4)
        stop = asyncio.Event()

        async def run(comp, sport_key):
            async with sem:
                if stop.is_set():
                    return
                if self._credits_low(cc):
                    summary["exhausted"] = True
                    stop.set()
                    return
                try:
                    # the events list is free: only spend credits when there are matches in the window
                    upcoming = await cc.get_json(f"{BASE}/sports/{sport_key}/events",
                                                 params={"apiKey": self.key, "dateFormat": "iso", **window}, ttl_seconds=1800)
                    if not upcoming:
                        summary["skipped"].append(f"{comp['name']} ({sport_key}): no matches in the window")
                        return
                    params = {"apiKey": self.key, "regions": regions, "markets": markets, "oddsFormat": "decimal",
                              "dateFormat": "iso", **window}
                    events = await cc.get_json(f"{BASE}/sports/{sport_key}/odds", params=params, ttl_seconds=600)
                except ProviderError as exc:
                    summary["errors"].append(f"{comp['name']}: {exc}")
                    if exc.kind == "quota":
                        summary["exhausted"] = True
                    if exc.fatal:
                        stop.set()
                    return
                if not isinstance(events, list):
                    summary["errors"].append(f"{comp['name']}: unexpected response")
                    return
                for ev in events:
                    try:
                        n = self._ingest(ev, comp, sport_key, now)
                    except Exception as exc:  # one malformed event must not stop the refresh
                        log.warning("Skipping event %s: %s", ev.get("id") if isinstance(ev, dict) else "?", exc)
                        continue
                    if n is not None:
                        summary["fixtures"] += 1
                        summary["quotes"] += n

        await asyncio.gather(*(run(c, k) for c, k in jobs))
        summary["results"] = await self._scores(cc, competitions, now, summary)
        status = {"last_ok": now.isoformat()} if not summary["errors"] else {"last_error": summary["errors"][-1], "last_error_at": now.isoformat()}
        if cc.last_headers.get("x-requests-remaining") is not None:
            status["remaining"] = cc.last_headers.get("x-requests-remaining")
            status["used"] = cc.last_headers.get("x-requests-used")
        status["exhausted"] = summary["exhausted"]
        self.repo.update_provider_status(NAME, **status)
        return summary

    def _credits_low(self, cc: CachedClient) -> bool:
        remaining = cc.last_headers.get("x-requests-remaining")
        if remaining is None:
            remaining = self.repo.provider_status().get(NAME, {}).get("remaining")
        try:
            return remaining is not None and float(remaining) < 5
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------
    def _ingest(self, ev: dict, comp: dict, sport_key: str, now: datetime) -> Optional[int]:
        home, away = ev["home_team"], ev["away_team"]
        kickoff = _parse_time(ev["commence_time"])
        if kickoff is None or kickoff <= now:
            return None  # BetFinder is pre-match only
        # the exact sport key (e.g. tennis_atp_paris for the pattern tennis_atp_*) is needed to fetch results
        candidate = Fixture(id="", sport=comp["sport"], competition_id=comp["id"], competition_name=comp["name"],
                            home=home, away=away, kickoff=kickoff,
                            league_refs={NAME: {"sport_key": str(ev.get("sport_key") or sport_key)}})
        fx, swapped = self.repo.merge_fixture(candidate, NAME, str(ev["id"]))
        chosen: dict[tuple[str, str], int] = {}
        quotes: list[Quote] = []
        for bm in ev.get("bookmakers", []) or []:
            reg_key, rank = self.registry.from_odds_api(bm.get("key", ""), bm.get("title", ""))
            for mk in bm.get("markets", []) or []:
                parsed = self._parse_market(mk, home, away, comp["sport"])
                if not parsed:
                    continue
                updated = _parse_time(mk.get("last_update") or bm.get("last_update"))
                for market, line, sel, price in parsed:
                    if swapped:
                        line, sel = swap_sides(market, line, sel)
                    mid = market_id(market, line)
                    prev = chosen.get((reg_key, mid))
                    if prev is not None and prev < rank:
                        continue  # a preferred feed for this bookmaker already supplied it
                    if prev is not None and prev > rank:
                        quotes = [q for q in quotes if not (q.bookmaker == reg_key and q.market_id == mid)]
                    chosen[(reg_key, mid)] = rank
                    try:
                        quotes.append(Quote(fixture_id=fx.id, market=market, line=line, selection=sel, bookmaker=reg_key,
                                            odds=price, source=NAME, fetched_at=now, book_updated_at=updated))
                    except ValueError:
                        continue
        self.repo.upsert_quotes(fx.id, quotes, replace_source=NAME)
        return len(quotes)

    @staticmethod
    def _side(name: str, home: str, away: str) -> Optional[str]:
        if name == home:
            return "home"
        if name == away:
            return "away"
        if name and name.lower() == "draw":
            return "draw"
        sh, sa = team_similarity(name, home), team_similarity(name, away)
        if max(sh, sa) >= 0.85 and abs(sh - sa) > 0.1:
            return "home" if sh > sa else "away"
        return None

    def _parse_market(self, mk: dict, home: str, away: str, sport: str) -> list[tuple]:
        key = mk.get("key")
        out = []
        for oc in mk.get("outcomes", []) or []:
            name, price, point = oc.get("name", ""), oc.get("price"), oc.get("point")
            if price is None:
                continue
            if key == "h2h":
                side = self._side(name, home, away)
                if side:
                    out.append(("h2h", None, side, price))
            elif key == "totals" and point is not None and name.lower() in ("over", "under"):
                out.append(("totals", normalise_line(point), name.lower(), price))
            elif key == "spreads" and point is not None:
                side = self._side(name, home, away)
                if side == "home":
                    out.append(("spreads", normalise_line(point), "home", price))
                elif side == "away":
                    out.append(("spreads", normalise_line(-float(point)), "away", price))
        if key == "h2h":
            has_draw_outcome = any(o[2] == "draw" for o in out)
            if has_draw(sport) and not has_draw_outcome and len(out) == 2:
                out = [("dnb", None, o[2], o[3]) for o in out]  # 2-way football/handball market = draw no bet
            elif not has_draw(sport) and has_draw_outcome:
                out = [("h2h3", None, o[2], o[3]) for o in out]  # e.g. boxing priced with the draw
        return out

    async def _scores(self, cc: CachedClient, competitions: list[dict], now: datetime, summary: dict) -> int:
        """Fetch results only for sport keys that have unsettled past fixtures (saves credits)."""
        patterns = {c["id"]: c.get("providers", {}).get("the_odds_api") for c in competitions}
        pending: dict[str, list[Fixture]] = {}
        for fx in self.repo.fixtures():
            if not (fx.refs.get(NAME) and fx.competition_id in patterns and fx.status in ("scheduled", "live")
                    and now - timedelta(days=3) < fx.kickoff < now - timedelta(hours=2)):
                continue
            sport_key = (fx.league_refs.get(NAME) or {}).get("sport_key") or patterns[fx.competition_id]
            if sport_key and "*" not in sport_key:  # a wildcard pattern is not a real sport key
                pending.setdefault(sport_key, []).append(fx)
        settled = 0
        for sport_key, fixtures in pending.items():
            try:
                data = await cc.get_json(f"{BASE}/sports/{sport_key}/scores",
                                         params={"apiKey": self.key, "daysFrom": 3, "dateFormat": "iso"}, ttl_seconds=1800)
            except ProviderError as exc:
                summary["errors"].append(f"{sport_key} scores: {exc}")
                continue
            by_ref = {fx.refs[NAME]: fx for fx in fixtures}
            for ev in data if isinstance(data, list) else []:
                fx = by_ref.get(str(ev.get("id")))
                if not fx or not ev.get("completed") or not ev.get("scores"):
                    continue
                scores = {s.get("name"): s.get("score") for s in ev["scores"]}
                try:
                    h, a = int(scores[ev["home_team"]]), int(scores[ev["away_team"]])
                except (KeyError, TypeError, ValueError):
                    continue
                # Provider home/away may be swapped relative to our fixture (it is the same match: compare the
                # club names only - our fixture may carry a "(W)" that this provider leaves out)
                if (team_similarity(ev["home_team"], fx.home, fx.sport, ignore_squad=True)
                        < team_similarity(ev["home_team"], fx.away, fx.sport, ignore_squad=True)):
                    h, a = a, h
                self.repo.set_result(fx.id, h, a)
                settled += 1
        return settled
