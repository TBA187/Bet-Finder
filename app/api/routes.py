"""REST API used by the web UI (all routes are under /api)."""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.config import APP_VERSION
from app.core.markets import LINE_MARKETS, market_id, normalise_line, selections_for, supported_markets
from app.core.models import Absence, Fixture, LineupInfo, Quote, utcnow
from app.core.names import find_fixture, search_score, slug
from app.core.odds_math import validate_odds
from app.core.sports import SPORTS, catalogue, is_sport
from app.i18n import lang_of, tr
from app.providers.http import ProviderError
from app.services import builder as bet_builder
from app.services import insights, notify
from app.services.auth import CURRENT_USER
from app.services.repository import fixture_id_for

MARKET = Literal["h2h", "h2h3", "dnb", "totals", "spreads", "btts"]


def _sport(v: Optional[str]) -> Optional[str]:
    if v is not None and not is_sport(v):
        raise ValueError(f"unsupported sport {v}")
    return v


# ---------------------------------------------------------------- request models
class PriceRow(BaseModel):
    bookmaker: str = Field(..., min_length=1, max_length=60)
    prices: dict[str, float]

    @field_validator("prices")
    @classmethod
    def _prices(cls, v):
        if not v:
            raise ValueError("Enter at least one price")
        return {k: validate_odds(o) for k, o in v.items()}


class MatchRef(BaseModel):
    fixture_id: Optional[str] = None
    sport: Optional[str] = None
    home: Optional[str] = Field(None, max_length=80)
    away: Optional[str] = Field(None, max_length=80)
    competition_id: Optional[str] = Field(None, max_length=80)
    competition_name: Optional[str] = Field(None, max_length=80)
    kickoff: Optional[datetime] = None
    market: MARKET
    line: Optional[float] = None

    @field_validator("sport")
    @classmethod
    def _v_sport(cls, v):
        return _sport(v)


class ManualOdds(MatchRef, PriceRow):
    pass


class ManualOddsMulti(MatchRef):
    rows: list[PriceRow] = Field(..., min_length=1, max_length=20)


class AbsenceIn(BaseModel):
    team: Literal["home", "away"]
    player: str = Field(..., min_length=1, max_length=80)
    kind: Literal["injury", "suspension", "doubtful", "other"] = "injury"
    importance: Literal["key", "regular", "rotation", "unknown"] = "unknown"
    reason: str = Field("", max_length=120)


class LineupIn(BaseModel):
    status: Literal["confirmed", "expected", "unknown"] = "unknown"
    formation: str = Field("", max_length=20)
    players: list[str] | str = Field(default_factory=list)
    rotated: Optional[bool] = None


class XgIn(BaseModel):
    xg_for: Optional[float] = Field(None, ge=0, le=10)
    xg_against: Optional[float] = Field(None, ge=0, le=10)
    games: int = Field(6, ge=1, le=60)


class ResultIn(BaseModel):
    score_home: int = Field(..., ge=0, le=500)
    score_away: int = Field(..., ge=0, le=500)
    status: Literal["finished", "cancelled", "postponed"] = "finished"


class TrackSelection(BaseModel):
    fixture_id: str
    market_id: str
    selection: str
    bookmaker: Optional[str] = None
    odds: Optional[float] = None
    stake: float = Field(1.0, gt=0, le=1e7)
    note: str = Field("", max_length=200)


class TrackCoupon(BaseModel):
    coupon_id: str
    stake: float = Field(1.0, gt=0, le=1e7)


class SettleIn(BaseModel):
    status: Literal["won", "lost", "void", "pending"]


class CompetitionToggle(BaseModel):
    enabled: bool


class BuilderLeg(BaseModel):
    fixture_id: str
    market_id: str
    selection: str
    odds: Optional[float] = None


class BuilderIn(BaseModel):
    legs: list[BuilderLeg] = Field(..., min_length=1, max_length=12)
    offered_odds: Optional[float] = Field(None, gt=1.0, le=100000)


def build_router(c) -> APIRouter:  # c: Container
    r = APIRouter()

    # ------------------------------------------------------------ helpers
    def settings() -> dict:
        s = c.settings.get()
        user = CURRENT_USER.get()
        if user and user.get("language") in ("en", "da"):
            s = {**s, "language": user["language"]}  # texts in the signed-in person's language
        return s

    def owner() -> dict:
        """Whose bet tracker: every account has its own.  Super admins also see bets
        tracked before accounts existed."""
        user = CURRENT_USER.get() or {}
        return {"owner": user.get("id"), "legacy": user.get("role") == "superadmin"}

    def lang() -> str:
        return lang_of(settings())

    def fixture_or_404(fid: str) -> Fixture:
        fx = c.repo.fixture(fid)
        if not fx:
            raise HTTPException(404, tr(lang(), "err_not_found"))
        return fx

    def compact(a: dict) -> dict:
        main = next((m for m in a["markets"] if m["market"] in ("h2h", "dnb", "h2h3")), a["markets"][0] if a["markets"] else None)
        values = [s for s in a["selections"] if s["ev_pct"] is not None and s["ev_pct"] > 0]
        devs = [s["market_ev_pct"] for s in a["selections"] if s["market_ev_pct"] is not None]
        return {
            "fixture": a["fixture"], "competition": a["competition"], "confidence": a["confidence"],
            "headline": a["headline"], "flags": a["flags"], "odds_updated_at": a["odds_updated_at"],
            "data_status": a["data_status"],
            "value_count": sum(1 for s in a["selections"] if s["qualifies"]),
            "positive_ev_count": len(values),
            "best_ev_pct": max((s["ev_pct"] for s in values), default=None),
            "max_deviation_pct": max(devs, default=None),
            "n_bookmakers": len({b["bookmaker"] for m in a["markets"] for b in m["books"]}),
            "sources": sorted({src for m in a["markets"] for b in m["books"] for src in b["source"]}),
            "main_market": None if not main else {
                "label": main["label"], "selections": main["selections"], "selection_labels": main["selection_labels"],
                "best": main["best"], "consensus": main["consensus"]["probs"],
            },
            "agents_with_data": sum(1 for ag in a["agents"] if ag["status"] in ("ok", "partial")),
        }

    def resolve_fixture(body: MatchRef, now: datetime, s: dict) -> tuple[Fixture, str]:
        L = lang_of(s)
        if body.fixture_id:
            return fixture_or_404(body.fixture_id), "selected match"
        if not (body.sport and body.home and body.away):
            raise HTTPException(400, tr(L, "err_choose_match"))
        pool = [f for f in c.repo.fixtures(include_demo=False)
                if f.kickoff > now - timedelta(hours=3) and f.status in ("scheduled", "live")]
        kickoff = body.kickoff.astimezone(timezone.utc) if body.kickoff else None
        hit = find_fixture(body.sport, body.home, body.away, kickoff, pool,
                           tolerance=timedelta(hours=6) if kickoff else timedelta(days=30))
        if hit:
            fx, score, swapped = hit
            if swapped:
                raise HTTPException(409, tr(L, "err_reversed", name=fx.name))
            return fx, f"matched by team names ({score:.0%})"
        if not kickoff:
            raise HTTPException(400, tr(L, "err_need_kickoff"))
        if kickoff <= now:
            raise HTTPException(400, tr(L, "err_future"))
        comp = c.registry.competition(body.competition_id) if body.competition_id else None
        if comp and comp["sport"] != body.sport:
            raise HTTPException(400, tr(L, "err_comp_sport"))
        cid = comp["id"] if comp else f"custom.{body.sport}.{slug(body.competition_name or 'other')}"
        cname = comp["name"] if comp else (body.competition_name or "Other")
        fx = Fixture(id=fixture_id_for(body.sport, body.home.strip(), body.away.strip(), kickoff), sport=body.sport,
                     competition_id=cid, competition_name=cname, home=body.home.strip(), away=body.away.strip(),
                     kickoff=kickoff, manual=True, is_demo=False, refs={"manual": "1"})
        c.repo.save_fixture(fx)
        return fx, "new match created"

    def save_rows(fx: Fixture, body: MatchRef, rows: list[PriceRow], now: datetime, s: dict) -> tuple[str, list[str]]:
        L = lang_of(s)
        if fx.kickoff <= now:
            raise HTTPException(400, tr(L, "err_started"))
        if body.market not in supported_markets(fx.sport):
            raise HTTPException(400, tr(L, "err_market_sport"))
        line = normalise_line(body.line) if body.market in LINE_MARKETS else None
        if body.market in LINE_MARKETS and line is None:
            raise HTTPException(400, tr(L, "err_line"))
        valid = set(selections_for(fx.sport, body.market))
        books = []
        for row in rows:
            bad = set(row.prices) - valid
            if bad:
                raise HTTPException(400, tr(L, "err_selections", bad=sorted(bad), ok=sorted(valid)))
        for row in rows:
            bookmaker = c.registry.from_name(row.bookmaker)
            quotes = [Quote(fixture_id=fx.id, market=body.market, line=line, selection=sel, bookmaker=bookmaker, odds=o,
                            source="manual", fetched_at=now) for sel, o in row.prices.items()]
            c.repo.upsert_quotes(fx.id, quotes)
            books.append(bookmaker)
        return market_id(body.market, line), books

    def manual_result(fx: Fixture, mid: str, books: list[str], matched_by: str, s: dict) -> dict:
        a = c.analysis.analyze(fx, s, utcnow())
        market = next((m for m in a["markets"] if m["market_id"] == mid), None)
        return {"fixture": a["fixture"], "matched_by": matched_by, "bookmakers": books,
                "bookmaker": books[0] if books else None,
                "bookmaker_names": [c.registry.bookmaker_name(b) for b in books],
                "bookmaker_name": c.registry.bookmaker_name(books[0]) if books else None,
                "market": market, "selections": [x for x in a["selections"] if x["market_id"] == mid],
                "margin_pct": next((b["margin_pct"] for b in (market or {}).get("books", []) if b["bookmaker"] == books[0]), None)
                if books else None}

    # ---------------------------------------------------------------- status
    @r.get("/health")
    def health():
        return {"ok": True, "version": APP_VERSION}

    @r.get("/catalog")
    def catalog():
        return catalogue()

    @r.get("/status")
    def status():
        s = settings()
        pstatus = c.repo.provider_status()
        now = utcnow()
        fixtures = c.repo.fixtures(include_demo=False)
        usage = insights.usage(pstatus, s, c.refresh.providers(s), c.settings.secret_source, now)
        return {
            "version": APP_VERSION, "now": now.isoformat(), "language": lang_of(s),
            "storage": {"kind": c.store.kind, "persistent": c.store.persistent, "vercel": bool(os.environ.get("VERCEL"))},
            "cron_configured": bool(os.environ.get("CRON_SECRET")),
            "providers": usage["providers"], "manual_entry": True, "scraper_mode": s["scraper_mode"],
            "last_refresh": c.refresh.last(), "refresh_running": c.refresh.running,
            "fixtures": len(fixtures), "fixtures_with_odds": sum(1 for fx in fixtures if c.repo.quotes(fx.id)),
            "registry_errors": c.registry.errors,
            "any_live_source": any(p["configured"] and p["method"] == "api" for p in usage["providers"]),
            "notifications": notify.enabled(s),
        }

    @r.post("/refresh")
    async def refresh():
        return await c.refresh.run()

    @r.get("/cron/refresh")
    async def cron_refresh(request: Request):
        """For schedulers (Vercel Cron, GitHub Actions, cron-job.org).
        Send header  Authorization: Bearer <CRON_SECRET>."""
        secret = os.environ.get("CRON_SECRET", "").strip()
        if secret:
            sent = request.headers.get("authorization", "")
            if not hmac.compare_digest(sent, f"Bearer {secret}"):
                raise HTTPException(401, "Invalid cron secret")
        elif os.environ.get("VERCEL"):
            raise HTTPException(401, "Set CRON_SECRET in the Vercel environment variables first")
        result = await c.refresh.run()
        return {"status": result.get("status"), "at": result.get("at"), "odds_methods": result.get("odds_methods"),
                "quotes": sum(p.get("quotes", 0) for p in result.get("providers", [])),
                "errors": [e for p in result.get("providers", []) for e in p.get("errors", [])][:5],
                "notifications": result.get("notifications")}

    @r.post("/providers/{name}/test")
    async def test_provider(name: Literal["the_odds_api", "api_sports", "odds_api_io", "oddspapi"]):
        """Check a key with the cheapest call each service offers.  The key is never echoed back."""
        import httpx
        s = settings()
        key = s[{"the_odds_api": "odds_api_key", "api_sports": "api_sports_key", "odds_api_io": "odds_api_io_key",
                 "oddspapi": "oddspapi_key"}[name]]
        if not key:
            return {"ok": False, "code": "no_key"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                if name == "the_odds_api":  # free call
                    resp = await client.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": key})
                    info = {"remaining": resp.headers.get("x-requests-remaining"), "used": resp.headers.get("x-requests-used")}
                elif name == "api_sports":  # /status does not count against the quota
                    resp = await client.get("https://v3.football.api-sports.io/status", headers={"x-apisports-key": key})
                    data = resp.json() if resp.status_code == 200 else {}
                    if data.get("errors"):
                        return {"ok": False, "code": "rejected", "detail": str(data["errors"])[:200]}
                    sub = (data.get("response") or {}).get("subscription") or {}
                    req = (data.get("response") or {}).get("requests") or {}
                    info = {"plan": sub.get("plan"), "requests_today": req.get("current"), "limit_day": req.get("limit_day")}
                    if str(sub.get("plan", "")).lower() == "free":
                        return {"ok": True, "code": "free_plan", "info": info}
                elif name == "odds_api_io":
                    resp = await client.get("https://api.odds-api.io/v3/bookmakers/selected", params={"apiKey": key})
                    info = {}
                else:  # oddspapi: uses 1 of the monthly free requests
                    resp = await client.get("https://api.oddspapi.io/v4/sports", params={"apiKey": key})
                    info = {}
        except httpx.HTTPError as exc:
            return {"ok": False, "code": "network", "detail": type(exc).__name__}
        now = utcnow().isoformat()
        if resp.status_code in (401, 403):
            c.repo.update_provider_status(name, last_error=f"API key rejected (HTTP {resp.status_code})", last_error_at=now)
            return {"ok": False, "code": "rejected"}
        if resp.status_code >= 400:
            return {"ok": False, "code": "error", "detail": f"HTTP {resp.status_code}"}
        c.repo.update_provider_status(name, last_ok=now)  # a working key counts as connected
        return {"ok": True, "code": "ok", "info": info}

    @r.get("/usage")
    def usage():
        s = settings()
        return insights.usage(c.repo.provider_status(), s, c.refresh.providers(s), c.settings.secret_source, utcnow())

    @r.get("/credits-plan")
    def credits_plan():
        return insights.credit_plan(settings(), c.registry.competitions())

    # ------------------------------------------------------------- dashboard
    @r.get("/dashboard")
    def dashboard():
        s = settings()
        now = utcnow()
        analyses = c.analyses(now, s)
        picks = c.daily_picks(now, s, analyses)
        tracker = c.tracker.overview(s, **owner())["summary"]
        return insights.dashboard(analyses, picks, tracker, status())

    @r.get("/agents")
    def agents():
        s = settings()
        return insights.agents_overview(c.analyses(utcnow(), s))

    @r.get("/arbitrage")
    def arbitrage(stake: float = Query(100.0, gt=0, le=1e7)):
        s = settings()
        return insights.arbitrage(c.analyses(utcnow(), s), stake)

    @r.get("/dropping-odds")
    def dropping(min_move: float = Query(5.0, ge=1, le=100)):
        return insights.dropping_odds(c.analyses(utcnow(), settings()), min_move)

    # --------------------------------------------------------------- scanner
    @r.get("/scanner")
    def scanner(sport: Optional[str] = None, category: Optional[str] = None, competition: Optional[str] = None,
                min_ev: Optional[float] = Query(None, ge=-100, le=500), min_deviation: Optional[float] = Query(None, ge=0, le=500),
                value_only: bool = False, bookmaker: Optional[str] = None,
                sort: Literal["value", "kickoff", "confidence", "deviation"] = "value"):
        s = settings()
        now = utcnow()
        items, no_odds = [], 0
        for fx in c.upcoming(now, s):
            if sport and fx.sport != sport:
                continue
            if category and SPORTS.get(fx.sport, {}).get("category") != category:
                continue
            if competition and fx.competition_id != competition:
                continue
            a = c.analysis.analyze(fx, s, now)
            if not a["markets"]:
                no_odds += 1
                continue
            item = compact(a)
            if bookmaker and not any(x["best"]["bookmaker"] == bookmaker and (x["ev_pct"] or -1) > 0 for x in a["selections"]):
                continue
            if value_only and item["value_count"] == 0:
                continue
            if min_ev is not None and (item["best_ev_pct"] is None or item["best_ev_pct"] < min_ev):
                continue
            if min_deviation is not None and (item["max_deviation_pct"] is None or item["max_deviation_pct"] < min_deviation):
                continue
            items.append(item)
        key = {"value": lambda i: -(i["best_ev_pct"] if i["best_ev_pct"] is not None else -999),
               "kickoff": lambda i: i["fixture"]["kickoff"], "confidence": lambda i: -i["confidence"],
               "deviation": lambda i: -(i["max_deviation_pct"] if i["max_deviation_pct"] is not None else -999)}[sort]
        items.sort(key=key)
        return {"items": items, "fixtures_without_odds": no_odds, "generated_at": now.isoformat()}

    @r.get("/fixtures/search")
    def search(q: str = Query(..., min_length=2, max_length=80), sport: Optional[str] = None, limit: int = Query(8, ge=1, le=25)):
        s = settings()
        now = utcnow()
        scored = []
        for fx in c.repo.fixtures(include_demo=False):
            if (sport and fx.sport != sport) or fx.kickoff < now - timedelta(hours=3):
                continue
            score = search_score(q, fx.home, fx.away)
            if score >= 0.5:
                scored.append((score, fx))
        scored.sort(key=lambda x: (-x[0], x[1].kickoff))
        return [{"id": fx.id, "sport": fx.sport, "home": fx.home, "away": fx.away, "competition": fx.competition_name,
                 "competition_id": fx.competition_id, "kickoff": fx.kickoff.isoformat(), "is_demo": fx.is_demo,
                 "score": round(sc, 2)} for sc, fx in scored[:limit]]

    @r.get("/fixtures/{fid}")
    def fixture_analysis(fid: str):
        fx = fixture_or_404(fid)
        a = c.analysis.analyze(fx, settings(), utcnow(), include_history=True)
        a["team_news"] = team_news(fid)
        return a

    @r.post("/fixtures/{fid}/fetch-more")
    async def fetch_more(fid: str):
        fx = fixture_or_404(fid)
        try:
            return await c.refresh.fetch_more(fx)
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from None

    # ------------------------------------------------------------ manual odds
    @r.post("/manual/odds")
    def manual_odds(body: ManualOdds):
        s, now = settings(), utcnow()
        fx, matched_by = resolve_fixture(body, now, s)
        mid, books = save_rows(fx, body, [PriceRow(bookmaker=body.bookmaker, prices=body.prices)], now, s)
        return manual_result(fx, mid, books, matched_by, s)

    @r.post("/manual/odds-multi")
    def manual_odds_multi(body: ManualOddsMulti):
        s, now = settings(), utcnow()
        fx, matched_by = resolve_fixture(body, now, s)
        mid, books = save_rows(fx, body, body.rows, now, s)
        return manual_result(fx, mid, books, matched_by, s)

    @r.delete("/fixtures/{fid}/quotes")
    def delete_quotes(fid: str, bookmaker: Optional[str] = None):
        fixture_or_404(fid)
        return {"removed": c.repo.delete_quotes(fid, source="manual", bookmaker=bookmaker)}

    # -------------------------------------------------------------- team news
    @r.get("/fixtures/{fid}/team-news")
    def team_news(fid: str):
        fixture_or_404(fid)
        manual = c.repo.team_news(fid)
        prov = c.repo.provider_news(fid)
        return {
            "absences": [a.model_dump(mode="json") for a in manual["absences"]],
            "lineups": {k: v.model_dump(mode="json") for k, v in manual["lineups"].items()},
            "xg": manual.get("xg", {}),
            "provider": {"absences": prov.get("absences", []), "absences_source": prov.get("absences_source"),
                         "absences_at": prov.get("absences_at"), "lineups": prov.get("lineups", {})},
        }

    @r.post("/fixtures/{fid}/absences")
    def add_absence(fid: str, body: AbsenceIn):
        fixture_or_404(fid)
        return c.repo.add_absence(fid, Absence(**body.model_dump(), source="manual")).model_dump(mode="json")

    @r.delete("/fixtures/{fid}/absences/{aid}")
    def remove_absence(fid: str, aid: str):
        if not c.repo.remove_absence(fid, aid):
            raise HTTPException(404, tr(lang(), "err_not_found"))
        return {"ok": True}

    @r.put("/fixtures/{fid}/lineups/{side}")
    def set_lineup(fid: str, side: Literal["home", "away"], body: LineupIn):
        fixture_or_404(fid)
        players = body.players if isinstance(body.players, list) else [p.strip() for p in body.players.split(",")]
        info = LineupInfo(status=body.status, formation=body.formation, players=[p for p in players if p][:25],
                          rotated=body.rotated, source="manual")
        return c.repo.set_lineup(fid, side, info).model_dump(mode="json")

    @r.put("/fixtures/{fid}/xg/{side}")
    def set_xg(fid: str, side: Literal["home", "away"], body: XgIn):
        fixture_or_404(fid)
        c.repo.set_xg(fid, side, body.xg_for, body.xg_against, body.games)
        return {"ok": True}

    @r.post("/fixtures/{fid}/result")
    def set_result(fid: str, body: ResultIn):
        fixture_or_404(fid)
        fx = c.repo.set_result(fid, body.score_home, body.score_away, body.status)
        c.tracker.refresh(settings())
        return {"ok": True, "fixture": c.analysis._fixture_dict(fx)}

    # ------------------------------------------------------------ daily picks
    @r.get("/daily-picks")
    def daily_picks():
        s = settings()
        return c.daily_picks(utcnow(), s)

    # -------------------------------------------------------------- builder
    @r.post("/builder")
    def builder(body: BuilderIn):
        s, now = settings(), utcnow()
        analyses = {}
        for leg in body.legs:
            if leg.fixture_id not in analyses:
                fx = c.repo.fixture(leg.fixture_id)
                if fx:
                    analyses[leg.fixture_id] = c.analysis.analyze(fx, s, now)
        comps = {comp["id"]: comp for comp in c.registry.competitions()}
        return bet_builder.evaluate([leg.model_dump() for leg in body.legs], analyses, comps, body.offered_odds)

    # ---------------------------------------------------------------- tracker
    @r.get("/tracker")
    def tracker():
        return c.tracker.overview(settings(), **owner())

    @r.post("/tracker/selection")
    def track_selection(body: TrackSelection):
        fx = fixture_or_404(body.fixture_id)
        a = c.analysis.analyze(fx, settings(), utcnow())
        sel = next((x for x in a["selections"] if x["market_id"] == body.market_id and x["selection"] == body.selection), None)
        if not sel:
            raise HTTPException(404, tr(lang(), "err_not_found"))
        bookmaker = c.registry.from_name(body.bookmaker) if body.bookmaker else sel["best"]["bookmaker"]
        odds = validate_odds(body.odds) if body.odds else sel["best"]["odds"]
        p = sel["final_prob"]
        leg = {"fixture_id": fx.id, "market": sel["market"], "line": sel["line"], "market_id": sel["market_id"],
               "market_label": sel["market_label"], "selection": sel["selection"], "selection_label": sel["label"],
               "bookmaker": bookmaker, "odds": odds, "probability": p, "fair_odds": sel["fair_odds"],
               "ev_pct": round((p * odds - 1) * 100, 2) if p else None}
        return c.tracker.track([leg], title=f"{fx.name}: {sel['label']}", stake=body.stake, source="manual", note=body.note,
                               owner=owner()["owner"])

    @r.post("/tracker/coupon")
    def track_coupon(body: TrackCoupon):
        data = c.store.read("daily_picks", {})
        for day in sorted(data, reverse=True):
            coupon = data[day].get("coupons", {}).get(body.coupon_id)
            if coupon:
                legs = [{k: leg[k] for k in ("fixture_id", "market", "line", "market_id", "market_label", "selection",
                                             "selection_label", "bookmaker", "odds", "probability", "fair_odds", "ev_pct")}
                        for leg in coupon["legs"]]
                return c.tracker.track(legs, title=tr(lang(), "tracker_daily", day=day, title=coupon["title"]),
                                       stake=body.stake, source="daily_picks", owner=owner()["owner"])
        raise HTTPException(404, tr(lang(), "err_not_found"))

    @r.post("/tracker/{item_id}/settle")
    def settle(item_id: str, body: SettleIn):
        return c.tracker.settle_manual(item_id, body.status, **owner())

    @r.delete("/tracker/{item_id}")
    def delete_tracked(item_id: str):
        if not c.tracker.delete(item_id, **owner()):
            raise HTTPException(404, tr(lang(), "err_not_found"))
        return {"ok": True}

    # ------------------------------------------------------- registry/settings
    @r.get("/bookmakers")
    def bookmakers():
        s = settings()
        provider_configured = {p.name: p.configured() for p in c.refresh.providers(s)}
        seen: dict[str, dict] = {}
        for _fid, quotes in c.repo.all_quotes().items():
            for q in quotes:
                entry = seen.setdefault(q.bookmaker, {"auto_last": None, "auto_quotes": 0, "manual_quotes": 0,
                                                      "scraped_quotes": 0, "via": set()})
                if q.source == "demo":
                    continue
                if q.source == "manual":
                    entry["manual_quotes"] += 1
                    continue
                if q.source == "scraper":
                    entry["scraped_quotes"] += 1
                else:
                    entry["auto_quotes"] += 1
                entry["via"].add(q.source)
                if entry["auto_last"] is None or q.fetched_at > entry["auto_last"]:
                    entry["auto_last"] = q.fetched_at
        now = utcnow()
        out = []

        def row(key, name, b, info):
            sources = b.get("sources", {}) if b else {}
            last = info.get("auto_last")
            if last and now - last < timedelta(hours=24):
                state = "scraped" if info.get("via") == {"scraper"} else "connected"
            elif any(provider_configured.get(src) for src in sources):
                state = "configured_no_data"
            elif sources:
                state = "provider_not_configured"
            else:
                state = "manual_only"
            return {"key": key, "name": name, "country": (b or {}).get("country", ""), "type": (b or {}).get("type", "soft"),
                    "priority": bool((b or {}).get("priority")), "sources": sorted(sources), "state": state,
                    "via": sorted(info.get("via", set())), "limitation": (b or {}).get("limitation"),
                    "note": (b or {}).get("note"), "last_automated_quote": last.isoformat() if last else None,
                    "automated_quotes": info.get("auto_quotes", 0), "scraped_quotes": info.get("scraped_quotes", 0),
                    "manual_quotes": info.get("manual_quotes", 0), "mine": key in (s.get("my_bookmakers") or []),
                    "unregistered": b is None}

        for b in c.registry.bookmakers():
            out.append(row(b["key"], b["name"], b, seen.get(b["key"], {})))
        known = {b["key"] for b in out}
        for key, info in seen.items():
            if key not in known and not key.startswith("demo_"):
                out.append(row(key, c.registry.bookmaker_name(key), None, info))
        out.sort(key=lambda b: (not b["priority"], b["state"] not in ("connected", "scraped"), b["name"].lower()))
        return out

    @r.get("/competitions")
    def competitions(sport: Optional[str] = None):
        s = settings()
        items = []
        for comp in c.registry.competitions(sport=sport):
            prov = comp.get("providers", {})
            items.append({"id": comp["id"], "sport": comp["sport"], "category": SPORTS.get(comp["sport"], {}).get("category"),
                          "name": comp["name"], "country": comp.get("country", ""), "enabled": comp["enabled"],
                          "odds_source": "the_odds_api" if prov.get("the_odds_api") else None,
                          "stats_source": "api_sports" if prov.get("api_sports") else None})
        plan = insights.credit_plan(s, c.registry.competitions())
        return {"items": items, "excluded_by_request": [], "odds_api_cost_per_refresh": plan["credits_per_refresh"],
                "credit_plan": plan}

    @r.put("/competitions/{cid}")
    def toggle_competition(cid: str, body: CompetitionToggle):
        try:
            c.registry.set_enabled(cid, body.enabled)
        except KeyError:
            raise HTTPException(404, tr(lang(), "err_not_found")) from None
        return {"ok": True}

    @r.get("/settings")
    def get_settings():
        return c.settings.public()

    @r.put("/settings")
    def put_settings(patch: dict):
        before = settings()
        result = c.settings.update(patch)
        after = settings()
        key_owner = {"odds_api_key": "the_odds_api", "api_sports_key": "api_sports", "odds_api_io_key": "odds_api_io",
                     "oddspapi_key": "oddspapi"}
        for key, provider in key_owner.items():
            if before.get(key) != after.get(key):  # new or removed key: old errors no longer apply
                c.repo.reset_provider_status(provider)
        return result

    @r.post("/notify/test")
    async def notify_test():
        s = settings()
        if not notify.enabled(s):
            raise HTTPException(400, "Configure Telegram or ntfy first")
        return {"results": await notify.test(s)}

    @r.get("/markets/{sport}")
    def markets_for(sport: str):
        if not is_sport(sport):
            raise HTTPException(404, tr(lang(), "err_not_found"))
        return [{"market": m, "selections": list(selections_for(sport, m)), "needs_line": m in LINE_MARKETS}
                for m in supported_markets(sport)]

    return r
