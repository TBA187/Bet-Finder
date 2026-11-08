"""The Odds API (https://the-odds-api.com) - live pre-match odds from many
European bookmakers (NordicBet, Betsson, Unibet, Pinnacle, Betfair, ...).

Quota: each /odds call costs (#markets x #regions) credits; /sports is free.
The free plan has 500 credits per month, so BetFinder caches responses and
skips competitions that are out of season.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.core.markets import market_id, normalise_line, swap_sides
from app.core.models import Fixture, Quote
from app.core.names import team_similarity
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
    label = "The Odds API"

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
        for comp in competitions:
            sport_key = comp.get("providers", {}).get("the_odds_api")
            if not sport_key:
                continue
            if sport_key not in active:
                summary["skipped"].append(f"{comp['name']}: not in season at The Odds API")
                continue
            params = {"apiKey": self.key, "regions": regions, "markets": markets, "oddsFormat": "decimal",
                      "dateFormat": "iso", "commenceTimeFrom": _iso(now), "commenceTimeTo": _iso(horizon)}
            try:
                events = await cc.get_json(f"{BASE}/sports/{sport_key}/odds", params=params, ttl_seconds=600)
            except ProviderError as exc:
                summary["errors"].append(f"{comp['name']}: {exc}")
                continue
            if not isinstance(events, list):
                summary["errors"].append(f"{comp['name']}: unexpected response")
                continue
            for ev in events:
                try:
                    n = self._ingest(ev, comp, now)
                except Exception as exc:  # one malformed event must not stop the refresh
                    log.warning("Skipping event %s: %s", ev.get("id") if isinstance(ev, dict) else "?", exc)
                    continue
                if n is not None:
                    summary["fixtures"] += 1
                    summary["quotes"] += n
        summary["results"] = await self._scores(cc, competitions, now, summary)
        status = {"last_ok": now.isoformat()} if not summary["errors"] else {"last_error": summary["errors"][-1], "last_error_at": now.isoformat()}
        if cc.last_headers.get("x-requests-remaining") is not None:
            status["remaining"] = cc.last_headers.get("x-requests-remaining")
            status["used"] = cc.last_headers.get("x-requests-used")
        self.repo.update_provider_status(NAME, **status)
        return summary

    # ------------------------------------------------------------------
    def _ingest(self, ev: dict, comp: dict, now: datetime) -> Optional[int]:
        home, away = ev["home_team"], ev["away_team"]
        kickoff = _parse_time(ev["commence_time"])
        if kickoff is None or kickoff <= now:
            return None  # BetFinder is pre-match only
        candidate = Fixture(id="", sport=comp["sport"], competition_id=comp["id"], competition_name=comp["name"],
                            home=home, away=away, kickoff=kickoff)
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
        if key == "h2h" and sport != "basketball" and len(out) == 2 and all(o[2] != "draw" for o in out):
            out = [("dnb", None, o[2], o[3]) for o in out]  # 2-way football/handball market = draw no bet
        return out

    async def _scores(self, cc: CachedClient, competitions: list[dict], now: datetime, summary: dict) -> int:
        """Fetch results only for competitions that have unsettled past fixtures (saves credits)."""
        pending = {}
        for fx in self.repo.fixtures(include_demo=False):
            if fx.refs.get(NAME) and fx.status in ("scheduled", "live") and now - timedelta(days=3) < fx.kickoff < now - timedelta(hours=2):
                pending.setdefault(fx.competition_id, []).append(fx)
        settled = 0
        for comp in competitions:
            sport_key = comp.get("providers", {}).get("the_odds_api")
            if not sport_key or comp["id"] not in pending:
                continue
            try:
                data = await cc.get_json(f"{BASE}/sports/{sport_key}/scores",
                                         params={"apiKey": self.key, "daysFrom": 3, "dateFormat": "iso"}, ttl_seconds=1800)
            except ProviderError as exc:
                summary["errors"].append(f"{comp['name']} scores: {exc}")
                continue
            by_ref = {fx.refs[NAME]: fx for fx in pending[comp["id"]]}
            for ev in data if isinstance(data, list) else []:
                fx = by_ref.get(str(ev.get("id")))
                if not fx or not ev.get("completed") or not ev.get("scores"):
                    continue
                scores = {s.get("name"): s.get("score") for s in ev["scores"]}
                try:
                    h, a = int(scores[ev["home_team"]]), int(scores[ev["away_team"]])
                except (KeyError, TypeError, ValueError):
                    continue
                # Provider home/away may be swapped relative to our fixture
                if team_similarity(ev["home_team"], fx.home) < team_similarity(ev["home_team"], fx.away):
                    h, a = a, h
                self.repo.set_result(fx.id, h, a)
                settled += 1
        return settled
