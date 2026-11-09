"""API-Sports (https://api-sports.io): fixtures, odds, team statistics, recent
results, head-to-head, injuries/suspensions and lineups.

One key works for football (v3.football), basketball (v1.basketball) and
handball (v1.handball), but each sport has its own daily quota.  The free
plan typically only covers past seasons.  When that happens the provider's
error message is shown in the Sources tab instead of silently returning
nothing.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.core.markets import normalise_line, swap_sides
from app.core.models import Fixture, Quote, TeamStats, utcnow
from app.core.names import normalise_team
from app.providers.http import Budget, CachedClient, ProviderError
from app.services.registry import Registry
from app.services.repository import Repository

log = logging.getLogger("betfinder.api_sports")
NAME = "api_sports"
BASES = {
    "football": "https://v3.football.api-sports.io",
    "basketball": "https://v1.basketball.api-sports.io",
    "handball": "https://v1.handball.api-sports.io",
    "icehockey": "https://v1.hockey.api-sports.io",
}
BET_MAP = {
    "football": {"match winner": "h2h", "home/away": "dnb", "goals over/under": "totals",
                 "asian handicap": "spreads", "both teams score": "btts"},
    "basketball": {"home/away": "h2h", "3way result": "h2h3", "over/under": "totals", "asian handicap": "spreads"},
    "handball": {"3way result": "h2h", "home/away": "dnb", "over/under": "totals", "asian handicap": "spreads"},
    "icehockey": {"3way result": "h2h3", "home/away": "h2h", "over/under": "totals", "asian handicap": "spreads"},
}
STATUS = {
    "scheduled": {"NS", "TBD"},
    "finished": {"FT", "AET", "PEN", "AOT", "AP", "AW"},
    "postponed": {"PST", "POST"},
    "cancelled": {"CANC", "ABD", "AWD", "WO", "SUSP", "INTR"},
}
SUSPENSION_WORDS = ("suspend", "red card", "yellow card", "ban")


def _status(short: str) -> str:
    for status, codes in STATUS.items():
        if short in codes:
            return status
    return "live" if short else "unknown"


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _num(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    v = _num(value)
    return int(v) if v is not None else None


def _response(data: Any) -> Any:
    if not isinstance(data, dict):
        raise ProviderError("api_sports: unexpected response")
    errors = data.get("errors")
    if errors:
        if isinstance(errors, dict):
            msg = "; ".join(f"{k}: {v}" for k, v in errors.items())
            keys = {k.lower() for k in errors}
        else:
            msg = "; ".join(str(e) for e in errors)
            keys = set()
        kind = ("plan" if "plan" in keys else "auth" if keys & {"token", "key", "access"} else
                "quota" if keys & {"requests", "ratelimit"} else "http")
        raise ProviderError(f"api_sports: {msg}", kind)
    return data.get("response", [])


def _wdl(block: dict, venue: str) -> Optional[int]:
    v = (block or {}).get(venue)
    if isinstance(v, dict):
        return _int(v.get("total"))
    return _int(v)


class ApiSportsProvider:
    name = NAME
    label = "API-Sports · api-sports.io"

    def __init__(self, repo: Repository, registry: Registry, settings: dict):
        self.repo, self.registry, self.settings = repo, registry, settings
        self.key = settings.get("api_sports_key", "")

    def configured(self) -> bool:
        return bool(self.key)

    # ------------------------------------------------------------------ main
    async def refresh(self, client: httpx.AsyncClient, competitions: list[dict], now: datetime) -> dict:
        summary = {"provider": NAME, "fixtures": 0, "quotes": 0, "results": 0, "errors": [], "skipped": [], "requests": 0}
        if not self.configured():
            summary["skipped"].append("No API key configured")
            return summary
        status = self.repo.provider_status().get(NAME, {})
        per_sport_remaining = status.get("remaining_by_sport", {})
        plan_limited = {k: v for k, v in (status.get("plan_limited") or {}).items() if v.get("until", "") > now.isoformat()}
        auth_failed = False
        for sport in BASES:
            comps = [c for c in competitions if c["sport"] == sport and c.get("providers", {}).get("api_sports")]
            if not comps or auth_failed:
                continue
            if sport in plan_limited:
                summary["skipped"].append(f"{sport}: {plan_limited[sport]['message']} (paused 12h)")
                continue
            limit = int(self.settings["api_sports_max_requests_per_refresh"])
            remaining = _int(per_sport_remaining.get(sport))
            if remaining is not None:
                limit = min(limit, max(0, remaining - int(self.settings["api_sports_daily_reserve"])))
            budget = Budget(limit)
            cc = CachedClient(self.repo, f"{NAME}_{sport}", client, (self.key,))
            for comp in comps:
                try:
                    await self._competition(cc, budget, comp, now, summary)
                except ProviderError as exc:
                    summary["errors"].append(f"{comp['name']}: {exc}")
                    if exc.kind == "plan":
                        # the same plan restriction applies to every league of this sport: stop and pause 12h
                        plan_limited[sport] = {"message": str(exc).replace("api_sports: ", ""),
                                               "until": (now + timedelta(hours=12)).isoformat()}
                        break
                    if exc.fatal:
                        auth_failed = exc.kind == "auth"
                        break
                except Exception as exc:  # defensive: never let one league stop the refresh
                    log.exception("api_sports %s failed", comp["id"])
                    summary["errors"].append(f"{comp['name']}: internal error {type(exc).__name__}")
            summary["requests"] += budget.used
            if budget.skipped:
                summary["skipped"].append(f"{sport}: {len(budget.skipped)} requests skipped (budget/quota)")
            rem = cc.last_headers.get("x-ratelimit-requests-remaining")
            if rem is not None:
                per_sport_remaining[sport] = rem
        fields = {"remaining_by_sport": per_sport_remaining, "plan_limited": plan_limited}
        if summary["errors"]:
            fields.update(last_error=summary["errors"][-1], last_error_at=now.isoformat())
        else:
            fields.update(last_ok=now.isoformat())
        self.repo.update_provider_status(NAME, **fields)
        return summary

    async def _get(self, cc: CachedClient, sport: str, path: str, params: dict, ttl: int, budget: Budget, what: str):
        data = await cc.get_json(f"{BASES[sport]}{path}", params=params, headers={"x-apisports-key": self.key},
                                 ttl_seconds=ttl, budget=budget, what=what)
        return data

    async def _competition(self, cc, budget, comp, now, summary):
        sport = comp["sport"]
        league = await self._league(cc, budget, comp)
        if not league:
            summary["skipped"].append(f"{comp['name']}: league not found at API-Sports")
            return
        games = await self._fixtures(cc, budget, comp, league, now)
        horizon = now + timedelta(hours=int(self.settings["lookahead_hours"]))
        upcoming = []
        for g in games:
            try:
                fx = self._merge(comp, league, g)
            except Exception as exc:
                log.warning("bad api_sports fixture: %s", exc)
                continue
            summary["fixtures"] += 1
            if fx.status == "finished":
                summary["results"] += 1
            if fx.status == "scheduled" and now < fx.kickoff <= horizon:
                upcoming.append((fx, g))
        upcoming.sort(key=lambda x: x[0].kickoff)
        if not upcoming:
            return
        summary["quotes"] += await self._odds(cc, budget, comp, league, upcoming, now)
        if sport == "football" and league.get("coverage", {}).get("injuries", True):
            await self._injuries(cc, budget, comp, league, upcoming, now)
        for fx, g in upcoming:
            await self._team_stats(cc, budget, comp, league, fx, g)
            await self._h2h(cc, budget, comp, fx, g)
            if sport == "football" and fx.kickoff - now <= timedelta(minutes=90):
                await self._lineups(cc, budget, comp, fx, g)

    # --------------------------------------------------------------- leagues
    async def _league(self, cc, budget, comp) -> Optional[dict]:
        sport, spec = comp["sport"], comp["providers"]["api_sports"]
        if "league" in spec:
            items = _response(await self._get(cc, sport, "/leagues", {"id": spec["league"]}, 7 * 86400, budget, "leagues"))
        else:
            items = _response(await self._get(cc, sport, "/leagues", {"country": spec.get("country", "")}, 7 * 86400, budget, "leagues"))
            items = self._match_league(items, spec.get("names", []), sport)
        if not items:
            return None
        item = items[0]
        lg = item.get("league", item)
        seasons = item.get("seasons", []) or []
        season = self._current_season(seasons, sport)
        if season is None:
            return None
        return {"id": lg.get("id"), "name": lg.get("name"), "season": season["value"], "coverage": season.get("coverage", {})}

    @staticmethod
    def _match_league(items: list, names: list[str], sport: str) -> list:
        wanted = [normalise_team(n) for n in names]

        def nm(i):
            return normalise_team((i.get("league", i) or {}).get("name", ""))

        exact = [i for i in items if nm(i) in wanted]
        if exact:
            return exact
        loose = [i for i in items if any(w and w in nm(i) for w in wanted) and "women" not in nm(i)]
        return sorted(loose, key=lambda i: len(nm(i)))

    @staticmethod
    def _current_season(seasons: list[dict], sport: str) -> Optional[dict]:
        today = date.today().isoformat()
        norm = []
        for s in seasons:
            value = s.get("year", s.get("season"))
            if value is None:
                continue
            norm.append({"value": value, "start": s.get("start") or "", "end": s.get("end") or "",
                         "current": bool(s.get("current")), "coverage": s.get("coverage", {})})
        if not norm:
            return None
        for s in norm:
            if s["current"]:
                return s
        for s in norm:
            if s["start"] and s["end"] and s["start"] <= today <= s["end"]:
                return s
        return max(norm, key=lambda s: (s["end"], str(s["value"])))

    # -------------------------------------------------------------- fixtures
    async def _fixtures(self, cc, budget, comp, league, now) -> list[dict]:
        sport = comp["sport"]
        days_ahead = max(1, int(self.settings["lookahead_hours"]) // 24 + 1)
        start = (now - timedelta(days=2)).date()
        end = (now + timedelta(days=days_ahead)).date()
        out = []
        if sport == "football":
            items = _response(await self._get(cc, sport, "/fixtures", {"league": league["id"], "season": league["season"],
                                                                        "from": start.isoformat(), "to": end.isoformat(), "timezone": "UTC"},
                                              1200, budget, "fixtures"))
            for it in items:
                f, t, g, sc = it.get("fixture", {}), it.get("teams", {}), it.get("goals", {}), it.get("score", {})
                ft = sc.get("fulltime") or {}
                st = _status((f.get("status") or {}).get("short", ""))
                h, a = (ft.get("home"), ft.get("away")) if ft.get("home") is not None else (g.get("home"), g.get("away"))
                out.append({"id": f.get("id"), "kickoff": _parse_time(f.get("date")) or _parse_time(f.get("timestamp")),
                            "home": t["home"]["name"], "away": t["away"]["name"],
                            "home_id": t["home"]["id"], "away_id": t["away"]["id"], "status": st,
                            "score_home": _int(h), "score_away": _int(a)})
            return out
        day = start
        while day <= end:
            ttl = 7200 if day < now.date() else 1200
            try:
                items = _response(await self._get(cc, sport, "/games", {"league": league["id"], "season": league["season"],
                                                                         "date": day.isoformat(), "timezone": "UTC"},
                                                  ttl, budget, f"games {day}"))
            except ProviderError as exc:
                if "budget" in str(exc):
                    break
                raise
            for it in items:
                t, sc = it.get("teams", {}), it.get("scores", {})
                st = _status((it.get("status") or {}).get("short", ""))
                if sport == "basketball":
                    h, a = (sc.get("home") or {}).get("total"), (sc.get("away") or {}).get("total")
                else:
                    h, a = sc.get("home"), sc.get("away")
                    short = (it.get("status") or {}).get("short", "")
                    periods = it.get("periods") or {}
                    if short in ("AET", "AP") and periods.get("first") and periods.get("second"):
                        # 1X2 handball markets settle on regular time
                        h = (_int(periods["first"].get("home")) or 0) + (_int(periods["second"].get("home")) or 0)
                        a = (_int(periods["first"].get("away")) or 0) + (_int(periods["second"].get("away")) or 0)
                out.append({"id": it.get("id"), "kickoff": _parse_time(it.get("date")) or _parse_time(it.get("timestamp")),
                            "home": t["home"]["name"], "away": t["away"]["name"],
                            "home_id": t["home"]["id"], "away_id": t["away"]["id"], "status": st,
                            "score_home": _int(h), "score_away": _int(a)})
            day += timedelta(days=1)
        return out

    def _merge(self, comp, league, g) -> Fixture:
        candidate = Fixture(
            id="", sport=comp["sport"], competition_id=comp["id"], competition_name=comp["name"],
            home=g["home"], away=g["away"], kickoff=g["kickoff"], status=g["status"],
            score_home=g["score_home"] if g["status"] == "finished" else None,
            score_away=g["score_away"] if g["status"] == "finished" else None,
            team_refs={NAME: {"home": str(g["home_id"]), "away": str(g["away_id"])}},
            league_refs={NAME: {"league": str(league["id"]), "season": str(league["season"])}},
        )
        fx, swapped = self.repo.merge_fixture(candidate, NAME, str(g["id"]))
        g["_swapped"] = swapped
        g["_fixture_id"] = fx.id
        return fx

    # ------------------------------------------------------------------ odds
    async def _odds(self, cc, budget, comp, league, upcoming, now) -> int:
        sport = comp["sport"]
        by_game = {str(g["id"]): (fx, g) for fx, g in upcoming}
        responses = []
        if sport == "football":
            for day in sorted({fx.kickoff.date() for fx, _ in upcoming}):
                page, total = 1, 1
                while page <= min(total, 3):
                    try:
                        data = await self._get(cc, sport, "/odds", {"league": league["id"], "season": league["season"],
                                                                     "date": day.isoformat(), "page": page}, 900, budget, f"odds {day} p{page}")
                    except ProviderError as exc:
                        if "budget" in str(exc):
                            break
                        raise
                    responses.extend(_response(data))
                    total = int((data.get("paging") or {}).get("total") or 1)
                    page += 1
        else:
            for fx, g in upcoming:
                try:
                    data = await self._get(cc, sport, "/odds", {"game": g["id"], "league": league["id"], "season": league["season"]},
                                           900, budget, f"odds game {g['id']}")
                except ProviderError as exc:
                    if "budget" in str(exc):
                        break
                    raise
                responses.extend(_response(data))
        total_quotes = 0
        for item in responses:
            ref = str((item.get("fixture") or item.get("game") or {}).get("id"))
            if ref not in by_game:
                continue
            fx, g = by_game[ref]
            quotes = self._parse_odds(item, fx, sport, g.get("_swapped", False), now)
            self.repo.upsert_quotes(fx.id, quotes, replace_source=NAME)
            total_quotes += len(quotes)
        return total_quotes

    def _parse_odds(self, item: dict, fx: Fixture, sport: str, swapped: bool, now: datetime) -> list[Quote]:
        updated = _parse_time(item.get("update"))
        quotes = []
        mapping = BET_MAP[sport]
        for bm in item.get("bookmakers", []) or []:
            reg_key = self.registry.from_name(bm.get("name", ""))
            for bet in bm.get("bets", []) or []:
                market = mapping.get(str(bet.get("name", "")).strip().lower())
                if not market:
                    continue
                for val in bet.get("values", []) or []:
                    parsed = self._parse_value(market, str(val.get("value", "")))
                    odd = _num(val.get("odd"))
                    if not parsed or odd is None:
                        continue
                    line, sel = parsed
                    if swapped:
                        line, sel = swap_sides(market, line, sel)
                    try:
                        quotes.append(Quote(fixture_id=fx.id, market=market, line=line, selection=sel, bookmaker=reg_key,
                                            odds=odd, source=NAME, fetched_at=now, book_updated_at=updated))
                    except ValueError:
                        continue
        # de-duplicate (some feeds repeat values)
        uniq = {}
        for q in quotes:
            uniq[q.key] = q
        return list(uniq.values())

    @staticmethod
    def _parse_value(market: str, value: str) -> Optional[tuple[Optional[float], str]]:
        v = value.strip()
        low = v.lower()
        if market in ("h2h", "h2h3"):
            sel = {"home": "home", "1": "home", "draw": "draw", "x": "draw", "away": "away", "2": "away"}.get(low)
            return (None, sel) if sel else None
        if market == "dnb":
            sel = {"home": "home", "1": "home", "away": "away", "2": "away"}.get(low)
            return (None, sel) if sel else None
        if market == "btts":
            return (None, low) if low in ("yes", "no") else None
        parts = v.split()
        if len(parts) != 2:
            return None
        try:
            num = float(parts[1])
        except ValueError:
            return None
        if market == "totals" and parts[0].lower() in ("over", "under"):
            return normalise_line(num), parts[0].lower()
        if market == "spreads":
            if parts[0].lower() == "home":
                return normalise_line(num), "home"
            if parts[0].lower() == "away":
                return normalise_line(-num), "away"
        return None

    # -------------------------------------------------------------- injuries
    async def _injuries(self, cc, budget, comp, league, upcoming, now):
        by_ref = {str(g["id"]): (fx, g) for fx, g in upcoming}
        found: dict[str, list[dict]] = {fx.id: [] for fx, _ in upcoming}
        for day in sorted({fx.kickoff.date() for fx, _ in upcoming}):
            try:
                items = _response(await self._get(cc, "football", "/injuries", {"league": league["id"], "season": league["season"],
                                                                                  "date": day.isoformat()}, 7200, budget, f"injuries {day}"))
            except ProviderError as exc:
                if "budget" in str(exc):
                    return
                raise
            for it in items:
                ref = str((it.get("fixture") or {}).get("id"))
                if ref not in by_ref:
                    continue
                fx, g = by_ref[ref]
                team_id = (it.get("team") or {}).get("id")
                side = "home" if team_id == g["home_id"] else "away" if team_id == g["away_id"] else None
                if side is None:
                    continue
                if g.get("_swapped"):
                    side = "away" if side == "home" else "home"
                player = it.get("player") or {}
                reason = str(player.get("reason") or "")
                ptype = str(player.get("type") or "")
                kind = ("suspension" if any(w in reason.lower() for w in SUSPENSION_WORDS)
                        else "doubtful" if ptype.lower() == "questionable" else "injury")
                found[fx.id].append({"team": side, "player": player.get("name", "?"), "kind": kind, "importance": "unknown",
                                     "reason": reason, "source": NAME})
        for fid, absences in found.items():
            self.repo.save_provider_news(fid, {"absences": absences, "absences_source": NAME, "absences_at": utcnow().isoformat()})

    # --------------------------------------------------------------- lineups
    async def _lineups(self, cc, budget, comp, fx: Fixture, g: dict):
        try:
            items = _response(await self._get(cc, "football", "/fixtures/lineups", {"fixture": g["id"]}, 600, budget, "lineups"))
        except ProviderError as exc:
            if "budget" in str(exc):
                return
            raise
        if not items:
            return
        lineups = {}
        with self.repo.store.edit("team_lineups", {}) as last:
            for it in items:
                team_id = (it.get("team") or {}).get("id")
                side = "home" if team_id == g["home_id"] else "away" if team_id == g["away_id"] else None
                if side is None:
                    continue
                if g.get("_swapped"):
                    side = "away" if side == "home" else "home"
                players = [((p or {}).get("player") or {}).get("name", "") for p in it.get("startXI", []) or []]
                key = f"{comp['id']}:{team_id}"
                prev = last.get(key)
                changes = None
                if prev and prev.get("fixture_id") != fx.id and prev.get("players"):
                    changes = len(set(players) - set(prev["players"]))
                if not prev or prev.get("fixture_id") != fx.id:
                    # remember the previous lineup separately so re-fetching this fixture keeps the comparison
                    last[key] = {"fixture_id": fx.id, "players": players, "previous": prev}
                elif prev.get("previous") and prev["previous"].get("players"):
                    changes = len(set(players) - set(prev["previous"]["players"]))
                lineups[side] = {"status": "confirmed", "formation": it.get("formation") or "", "players": players,
                                 "changes_from_last": changes, "source": NAME, "updated_at": utcnow().isoformat()}
        if lineups:
            self.repo.save_provider_news(fx.id, {"lineups": lineups})

    # ----------------------------------------------------------------- stats
    async def _team_stats(self, cc, budget, comp, league, fx: Fixture, g: dict):
        sport = comp["sport"]
        for side in ("home", "away"):
            team_id = g[f"{side}_id"]
            name = g[side]
            key = f"{NAME}:{comp['id']}:{team_id}"
            cached = self.repo.team_stats(key)
            if cached and (utcnow() - cached.fetched_at) < timedelta(hours=12):
                continue
            try:
                stats = await self._fetch_stats(cc, budget, sport, league, team_id, name)
            except ProviderError as exc:
                if "budget" in str(exc):
                    return
                raise
            if stats:
                self.repo.save_team_stats(key, stats)

    async def _fetch_stats(self, cc, budget, sport, league, team_id, name) -> Optional[TeamStats]:
        params = {"league": league["id"], "season": league["season"], "team": team_id}
        path = {"basketball": "/statistics"}.get(sport, "/teams/statistics")
        resp = _response(await self._get(cc, sport, path, params, 12 * 3600, budget, f"stats {name}"))
        if isinstance(resp, list):
            resp = resp[0] if resp else {}
        stats = TeamStats(team=name, source=NAME, season=str(league["season"]))
        if sport == "football":
            fixtures = resp.get("fixtures") or {}
            goals = resp.get("goals") or {}
            played = fixtures.get("played") or {}
            stats.games_home, stats.games_away = _int(played.get("home")) or 0, _int(played.get("away")) or 0
            gf, ga = (goals.get("for") or {}).get("average") or {}, (goals.get("against") or {}).get("average") or {}
            stats.scored_home, stats.scored_away = _num(gf.get("home")), _num(gf.get("away"))
            stats.conceded_home, stats.conceded_away = _num(ga.get("home")), _num(ga.get("away"))
            for kind, attr in (("wins", "wins"), ("draws", "draws"), ("loses", "losses")):
                block = fixtures.get(kind) or {}
                setattr(stats, f"{attr}_home", _wdl(block, "home"))
                setattr(stats, f"{attr}_away", _wdl(block, "away"))
            stats.clean_sheets = _int((resp.get("clean_sheet") or {}).get("total"))
            stats.failed_to_score = _int((resp.get("failed_to_score") or {}).get("total"))
        else:
            games = resp.get("games") or {}
            pts = resp.get("points") if sport == "basketball" else resp.get("goals")
            pts = pts or {}
            played = games.get("played") or {}
            stats.games_home, stats.games_away = _int(played.get("home")) or 0, _int(played.get("away")) or 0
            pf, pa = (pts.get("for") or {}).get("average") or {}, (pts.get("against") or {}).get("average") or {}
            stats.scored_home, stats.scored_away = _num(pf.get("home")), _num(pf.get("away"))
            stats.conceded_home, stats.conceded_away = _num(pa.get("home")), _num(pa.get("away"))
            for kind, attr in (("wins", "wins"), ("draws", "draws"), ("loses", "losses")):
                block = games.get(kind) or {}
                setattr(stats, f"{attr}_home", _wdl(block, "home"))
                setattr(stats, f"{attr}_away", _wdl(block, "away"))
        stats.recent = await self._recent(cc, budget, sport, league, team_id)
        if sport == "football" and self.settings.get("api_sports_fetch_xg", True):
            await self._xg(cc, budget, stats, team_id)
        return stats

    async def _xg(self, cc, budget, stats: TeamStats, team_id) -> None:
        """Average expected goals for/against over the last 5 matches (from match statistics).
        Match statistics never change, so each one is cached for 30 days."""
        xf, xa = [], []
        for row in [r for r in stats.recent if r.get("fixture_id")][-5:]:
            try:
                items = _response(await self._get(cc, "football", "/fixtures/statistics", {"fixture": row["fixture_id"]},
                                                  30 * 86400, budget, "xg"))
            except ProviderError:
                break
            mine = other = None
            for it in items:
                vals = {str(x.get("type", "")).lower(): x.get("value") for x in it.get("statistics", []) or []}
                xg = _num(vals.get("expected_goals"))
                if (it.get("team") or {}).get("id") == team_id:
                    mine = xg
                else:
                    other = xg
            if mine is not None and other is not None:
                xf.append(mine)
                xa.append(other)
        if len(xf) >= 3:
            stats.xg_for, stats.xg_against = round(sum(xf) / len(xf), 3), round(sum(xa) / len(xa), 3)
            stats.xg_games, stats.xg_source = len(xf), NAME

    async def _recent(self, cc, budget, sport, league, team_id) -> list[dict]:
        try:
            if sport == "football":
                items = _response(await self._get(cc, sport, "/fixtures", {"team": team_id, "last": 8}, 12 * 3600, budget, "recent"))
                rows = []
                for it in items:
                    f, t, gl = it.get("fixture", {}), it.get("teams", {}), it.get("goals", {})
                    if _status((f.get("status") or {}).get("short", "")) != "finished":
                        continue
                    home = t["home"]["id"] == team_id
                    rows.append({"date": f.get("date"), "venue": "H" if home else "A", "fixture_id": f.get("id"),
                                 "opponent": t["away" if home else "home"]["name"],
                                 "scored": _int(gl.get("home" if home else "away")), "conceded": _int(gl.get("away" if home else "home"))})
            else:
                items = _response(await self._get(cc, sport, "/games", {"team": team_id, "league": league["id"], "season": league["season"]},
                                                  12 * 3600, budget, "recent"))
                rows = []
                for it in items:
                    if _status((it.get("status") or {}).get("short", "")) != "finished":
                        continue
                    t, sc = it.get("teams", {}), it.get("scores", {})
                    home = t["home"]["id"] == team_id
                    if sport == "basketball":
                        hs, as_ = (sc.get("home") or {}).get("total"), (sc.get("away") or {}).get("total")
                    else:
                        hs, as_ = sc.get("home"), sc.get("away")
                    rows.append({"date": it.get("date"), "venue": "H" if home else "A",
                                 "opponent": t["away" if home else "home"]["name"],
                                 "scored": _int(hs if home else as_), "conceded": _int(as_ if home else hs)})
        except ProviderError as exc:
            if "budget" in str(exc):
                return []
            raise
        rows = [r for r in rows if r["scored"] is not None and r["conceded"] is not None]
        rows.sort(key=lambda r: r["date"] or "")
        return rows[-8:]

    async def _h2h(self, cc, budget, comp, fx: Fixture, g: dict):
        if self.repo.h2h(fx.id) is not None:
            return
        sport = comp["sport"]
        pair = f"{g['home_id']}-{g['away_id']}"
        try:
            if sport == "football":
                items = _response(await self._get(cc, sport, "/fixtures/headtohead", {"h2h": pair, "last": 6}, 86400, budget, "h2h"))
            else:
                items = _response(await self._get(cc, sport, "/games", {"h2h": pair}, 86400, budget, "h2h"))
        except ProviderError as exc:
            if "budget" in str(exc):
                return
            raise
        names = {g["home_id"]: fx.home if not g.get("_swapped") else fx.away,
                 g["away_id"]: fx.away if not g.get("_swapped") else fx.home}
        meetings = []
        for it in items:
            if sport == "football":
                f, t, gl = it.get("fixture", {}), it.get("teams", {}), it.get("goals", {})
                st = _status((f.get("status") or {}).get("short", ""))
                h, a, when = gl.get("home"), gl.get("away"), f.get("date")
            else:
                t, sc = it.get("teams", {}), it.get("scores", {})
                st = _status((it.get("status") or {}).get("short", ""))
                when = it.get("date")
                if sport == "basketball":
                    h, a = (sc.get("home") or {}).get("total"), (sc.get("away") or {}).get("total")
                else:
                    h, a = sc.get("home"), sc.get("away")
            if st != "finished":
                continue
            meetings.append({"date": when, "home": names.get(t["home"]["id"], t["home"]["name"]),
                             "away": names.get(t["away"]["id"], t["away"]["name"]),
                             "score_home": _int(h), "score_away": _int(a)})
        meetings.sort(key=lambda m: m["date"] or "", reverse=True)
        self.repo.save_h2h(fx.id, meetings[:6])
