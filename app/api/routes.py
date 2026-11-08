"""REST API used by the web UI (all routes are under /api)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.config import APP_VERSION
from app.core.markets import LINE_MARKETS, market_id, normalise_line, selections_for, supported_markets
from app.core.models import Absence, Fixture, LineupInfo, Quote, utcnow
from app.core.names import find_fixture, search_score, slug
from app.core.odds_math import validate_odds
from app.providers.demo import load_demo
from app.services.daily_picks import build_daily_picks
from app.services.repository import fixture_id_for

SPORT = Literal["football", "basketball", "handball"]


# ---------------------------------------------------------------- request models
class ManualOdds(BaseModel):
    fixture_id: Optional[str] = None
    sport: Optional[SPORT] = None
    home: Optional[str] = Field(None, max_length=80)
    away: Optional[str] = Field(None, max_length=80)
    competition_id: Optional[str] = Field(None, max_length=80)
    competition_name: Optional[str] = Field(None, max_length=80)
    kickoff: Optional[datetime] = None
    bookmaker: str = Field(..., min_length=1, max_length=60)
    market: Literal["h2h", "h2h3", "dnb", "totals", "spreads", "btts"]
    line: Optional[float] = None
    prices: dict[str, float]

    @field_validator("prices")
    @classmethod
    def _prices(cls, v):
        if not v:
            raise ValueError("Enter at least one price")
        return {k: validate_odds(o) for k, o in v.items()}


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


def build_router(c) -> APIRouter:  # c: Container
    r = APIRouter()

    # ------------------------------------------------------------ helpers
    def settings() -> dict:
        return c.settings.get()

    def fixture_or_404(fid: str) -> Fixture:
        fx = c.repo.fixture(fid)
        if not fx:
            raise HTTPException(404, "Fixture not found")
        return fx

    def upcoming(now: datetime, s: dict, include_started: bool = False) -> list[Fixture]:
        horizon = now + timedelta(hours=int(s["lookahead_hours"]))
        out = []
        for fx in c.repo.fixtures(include_demo=bool(s["demo_mode"])):
            if fx.status in ("cancelled", "postponed", "finished") and not include_started:
                continue
            if (fx.kickoff > now or include_started) and fx.kickoff <= horizon:
                out.append(fx)
        return sorted(out, key=lambda f: f.kickoff)

    def analyses(now: datetime, s: dict) -> list[dict]:
        out = []
        for fx in upcoming(now, s):
            out.append(c.analysis.analyze(fx, s, now))
        return out

    def compact(a: dict) -> dict:
        main = next((m for m in a["markets"] if m["market"] in ("h2h", "dnb", "h2h3")), a["markets"][0] if a["markets"] else None)
        values = [s for s in a["selections"] if s["ev_pct"] is not None and s["ev_pct"] > 0]
        return {
            "fixture": a["fixture"], "competition": a["competition"], "confidence": a["confidence"],
            "headline": a["headline"], "flags": a["flags"],
            "value_count": sum(1 for s in a["selections"] if s["qualifies"]),
            "positive_ev_count": len(values),
            "best_ev_pct": max((s["ev_pct"] for s in values), default=None),
            "n_bookmakers": len({b["bookmaker"] for m in a["markets"] for b in m["books"]}),
            "main_market": None if not main else {
                "label": main["label"], "selections": main["selections"], "selection_labels": main["selection_labels"],
                "best": main["best"], "consensus": main["consensus"]["probs"],
            },
            "agents_with_data": sum(1 for ag in a["agents"] if ag["status"] in ("ok", "partial")),
        }

    # ---------------------------------------------------------------- status
    @r.get("/health")
    def health():
        return {"ok": True, "version": APP_VERSION}

    @r.get("/status")
    def status():
        s = settings()
        pstatus = c.repo.provider_status()
        now = utcnow()
        fixtures = c.repo.fixtures(include_demo=bool(s["demo_mode"]))
        with_odds = sum(1 for fx in fixtures if c.repo.quotes(fx.id))
        providers = []
        for p in c.refresh.providers(s):
            st = pstatus.get(p.name, {})
            providers.append({
                "name": p.name, "label": p.label, "configured": p.configured(),
                "key_source": c.settings.secret_source("odds_api_key" if p.name == "the_odds_api" else "api_sports_key"),
                "last_ok": st.get("last_ok"), "last_error": st.get("last_error"), "last_error_at": st.get("last_error_at"),
                "remaining": st.get("remaining") if p.name == "the_odds_api" else st.get("remaining_by_sport"),
                "requests_today": sum(v.get("requests_by_day", {}).get(now.strftime("%Y-%m-%d"), 0)
                                      for k, v in pstatus.items() if k.startswith(p.name)),
            })
        return {
            "version": APP_VERSION, "now": now.isoformat(), "demo_mode": s["demo_mode"],
            "providers": providers, "manual_entry": True,
            "last_refresh": c.refresh.last(), "refresh_running": c.refresh.running,
            "fixtures": len(fixtures), "fixtures_with_odds": with_odds,
            "registry_errors": c.registry.errors, "auth_required": bool(c.token),
            "any_live_source": any(p["configured"] for p in providers),
        }

    @r.post("/refresh")
    async def refresh():
        return await c.refresh.run()

    # --------------------------------------------------------------- scanner
    @r.get("/scanner")
    def scanner(sport: Optional[SPORT] = None, competition: Optional[str] = None,
                min_ev: Optional[float] = Query(None, ge=-100, le=500), value_only: bool = False,
                bookmaker: Optional[str] = None, sort: Literal["value", "kickoff", "confidence"] = "value"):
        s = settings()
        now = utcnow()
        items = []
        no_odds = 0
        for fx in upcoming(now, s):
            if sport and fx.sport != sport:
                continue
            if competition and fx.competition_id != competition:
                continue
            a = c.analysis.analyze(fx, s, now)
            if not a["markets"]:
                no_odds += 1
                continue
            item = compact(a)
            if bookmaker:
                sels = [x for x in a["selections"] if x["best"]["bookmaker"] == bookmaker and (x["ev_pct"] or -1) > 0]
                if not sels:
                    continue
            if value_only and item["value_count"] == 0:
                continue
            if min_ev is not None and (item["best_ev_pct"] is None or item["best_ev_pct"] < min_ev):
                continue
            items.append(item)
        key = {"value": lambda i: -(i["best_ev_pct"] if i["best_ev_pct"] is not None else -999),
               "kickoff": lambda i: i["fixture"]["kickoff"], "confidence": lambda i: -i["confidence"]}[sort]
        items.sort(key=key)
        return {"items": items, "fixtures_without_odds": no_odds, "generated_at": now.isoformat()}

    @r.get("/fixtures/search")
    def search(q: str = Query(..., min_length=2, max_length=80), sport: Optional[SPORT] = None, limit: int = Query(8, ge=1, le=25)):
        s = settings()
        now = utcnow()
        scored = []
        for fx in c.repo.fixtures(include_demo=bool(s["demo_mode"])):
            if sport and fx.sport != sport:
                continue
            if fx.kickoff < now - timedelta(hours=3):
                continue
            score = search_score(q, fx.home, fx.away)
            if score >= 0.5:
                scored.append((score, fx))
        scored.sort(key=lambda x: (-x[0], x[1].kickoff))
        return [{"id": fx.id, "sport": fx.sport, "home": fx.home, "away": fx.away, "competition": fx.competition_name,
                 "kickoff": fx.kickoff.isoformat(), "is_demo": fx.is_demo, "score": round(sc, 2)} for sc, fx in scored[:limit]]

    @r.get("/fixtures/{fid}")
    def fixture_analysis(fid: str):
        fx = fixture_or_404(fid)
        a = c.analysis.analyze(fx, settings(), utcnow(), include_history=True)
        a["team_news"] = team_news(fid)
        return a

    # ------------------------------------------------------------ manual odds
    @r.post("/manual/odds")
    def manual_odds(body: ManualOdds):
        s = settings()
        now = utcnow()
        matched_by = "selected match"
        fx: Optional[Fixture] = None
        if body.fixture_id:
            fx = fixture_or_404(body.fixture_id)
        else:
            if not (body.sport and body.home and body.away):
                raise HTTPException(400, "Choose a match, or give sport, home and away teams")
            pool = [f for f in c.repo.fixtures(include_demo=bool(s["demo_mode"]))
                    if f.kickoff > now - timedelta(hours=3) and f.status in ("scheduled", "live")]
            kickoff = body.kickoff.astimezone(timezone.utc) if body.kickoff else None
            hit = find_fixture(body.sport, body.home, body.away, kickoff, pool,
                               tolerance=timedelta(hours=6) if kickoff else timedelta(days=30))
            if hit:
                fx, score, swapped = hit
                if swapped:
                    raise HTTPException(409, f"Found {fx.name} with home and away reversed. Select the match from the list.")
                matched_by = f"matched by team names ({score:.0%})"
            else:
                if not kickoff:
                    raise HTTPException(400, "Match not found. Add the kick-off time so it can be created.")
                if kickoff <= now:
                    raise HTTPException(400, "Kick-off must be in the future (BetFinder is pre-match only)")
                comp = c.registry.competition(body.competition_id) if body.competition_id else None
                if comp and comp["sport"] != body.sport:
                    raise HTTPException(400, "Competition belongs to another sport")
                cid = comp["id"] if comp else f"custom.{body.sport}.{slug(body.competition_name or 'other')}"
                cname = comp["name"] if comp else (body.competition_name or "Other")
                fx = Fixture(id=fixture_id_for(body.sport, body.home.strip(), body.away.strip(), kickoff), sport=body.sport,
                             competition_id=cid, competition_name=cname, home=body.home.strip(), away=body.away.strip(),
                             kickoff=kickoff, manual=True, is_demo=False, refs={"manual": "1"})
                c.repo.save_fixture(fx)
                matched_by = "new match created"
        if fx.kickoff <= now:
            raise HTTPException(400, "This match has already started; BetFinder only stores pre-match prices")
        if body.market not in supported_markets(fx.sport):
            raise HTTPException(400, f"Market {body.market} is not available for {fx.sport}")
        line = normalise_line(body.line) if body.market in LINE_MARKETS else None
        if body.market in LINE_MARKETS and line is None:
            raise HTTPException(400, "This market needs a line (for example 2.5)")
        valid = set(selections_for(fx.sport, body.market))
        bad = set(body.prices) - valid
        if bad:
            raise HTTPException(400, f"Invalid selections {sorted(bad)}; use {sorted(valid)}")
        bookmaker = c.registry.from_name(body.bookmaker)
        quotes = [Quote(fixture_id=fx.id, market=body.market, line=line, selection=sel, bookmaker=bookmaker, odds=o,
                        source="manual", fetched_at=now) for sel, o in body.prices.items()]
        c.repo.upsert_quotes(fx.id, quotes)
        a = c.analysis.analyze(fx, s, utcnow())
        mid = market_id(body.market, line)
        market = next((m for m in a["markets"] if m["market_id"] == mid), None)
        sels = [x for x in a["selections"] if x["market_id"] == mid]
        return {"fixture": a["fixture"], "matched_by": matched_by, "bookmaker": bookmaker,
                "bookmaker_name": c.registry.bookmaker_name(bookmaker), "market": market, "selections": sels,
                "margin_pct": next((b["margin_pct"] for b in (market or {}).get("books", []) if b["bookmaker"] == bookmaker), None)}

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
            "provider": {"absences": prov.get("absences", []), "absences_source": prov.get("absences_source"),
                         "lineups": prov.get("lineups", {})},
        }

    @r.post("/fixtures/{fid}/absences")
    def add_absence(fid: str, body: AbsenceIn):
        fixture_or_404(fid)
        return c.repo.add_absence(fid, Absence(**body.model_dump(), source="manual")).model_dump(mode="json")

    @r.delete("/fixtures/{fid}/absences/{aid}")
    def remove_absence(fid: str, aid: str):
        if not c.repo.remove_absence(fid, aid):
            raise HTTPException(404, "Absence not found")
        return {"ok": True}

    @r.put("/fixtures/{fid}/lineups/{side}")
    def set_lineup(fid: str, side: Literal["home", "away"], body: LineupIn):
        fixture_or_404(fid)
        players = body.players if isinstance(body.players, list) else [p.strip() for p in body.players.split(",")]
        info = LineupInfo(status=body.status, formation=body.formation, players=[p for p in players if p][:25],
                          rotated=body.rotated, source="manual")
        return c.repo.set_lineup(fid, side, info).model_dump(mode="json")

    @r.post("/fixtures/{fid}/result")
    def set_result(fid: str, body: ResultIn):
        fixture_or_404(fid)
        fx = c.repo.set_result(fid, body.score_home, body.score_away, body.status)
        c.tracker.refresh(settings())
        return {"ok": True, "fixture": c.analysis._fixture_dict(fx)}

    # ------------------------------------------------------------ daily picks
    def coupon_id(coupon: dict) -> str:
        key = json.dumps([(leg["fixture_id"], leg["market_id"], leg["selection"], leg["bookmaker"], leg["odds"]) for leg in coupon["legs"]])
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    @r.get("/daily-picks")
    def daily_picks():
        s = settings()
        now = utcnow()
        result = build_daily_picks(analyses(now, s), s, now)
        for coupon in result["coupons"]:
            coupon["id"] = coupon_id(coupon)
        with c.store.edit("daily_picks", {}) as data:
            day = data.setdefault(result["date"], {"coupons": {}, "latest": None})
            for coupon in result["coupons"]:
                day["coupons"][coupon["id"]] = coupon
            day["latest"] = {k: v for k, v in result.items() if k != "singles"}
            for old in sorted(data)[:-30]:
                data.pop(old, None)
        return result

    # ---------------------------------------------------------------- tracker
    @r.get("/tracker")
    def tracker():
        return c.tracker.overview(settings())

    @r.post("/tracker/selection")
    def track_selection(body: TrackSelection):
        fx = fixture_or_404(body.fixture_id)
        a = c.analysis.analyze(fx, settings(), utcnow())
        sel = next((x for x in a["selections"] if x["market_id"] == body.market_id and x["selection"] == body.selection), None)
        if not sel:
            raise HTTPException(404, "Selection not found for this match")
        bookmaker = c.registry.from_name(body.bookmaker) if body.bookmaker else sel["best"]["bookmaker"]
        odds = validate_odds(body.odds) if body.odds else sel["best"]["odds"]
        p = sel["final_prob"]
        leg = {"fixture_id": fx.id, "market": sel["market"], "line": sel["line"], "market_id": sel["market_id"],
               "market_label": sel["market_label"], "selection": sel["selection"], "selection_label": sel["label"],
               "bookmaker": bookmaker, "odds": odds, "probability": p, "fair_odds": sel["fair_odds"],
               "ev_pct": round((p * odds - 1) * 100, 2) if p else None}
        return c.tracker.track([leg], title=f"{fx.name}: {sel['label']}", stake=body.stake, source="manual", note=body.note)

    @r.post("/tracker/coupon")
    def track_coupon(body: TrackCoupon):
        data = c.store.read("daily_picks", {})
        for day in sorted(data, reverse=True):
            coupon = data[day].get("coupons", {}).get(body.coupon_id)
            if coupon:
                legs = [{"fixture_id": leg["fixture_id"], "market": leg["market"], "line": leg["line"], "market_id": leg["market_id"],
                         "market_label": leg["market_label"], "selection": leg["selection"], "selection_label": leg["selection_label"],
                         "bookmaker": leg["bookmaker"], "odds": leg["odds"], "probability": leg["probability"],
                         "fair_odds": leg["fair_odds"], "ev_pct": leg["ev_pct"]} for leg in coupon["legs"]]
                return c.tracker.track(legs, title=f"Daily pick {day} - {coupon['title']}", stake=body.stake, source="daily_picks")
        raise HTTPException(404, "Coupon not found (it may have changed). Reload Daily Picks.")

    @r.post("/tracker/{item_id}/settle")
    def settle(item_id: str, body: SettleIn):
        return c.tracker.settle_manual(item_id, body.status)

    @r.delete("/tracker/{item_id}")
    def delete_tracked(item_id: str):
        if not c.tracker.delete(item_id):
            raise HTTPException(404, "Not found")
        return {"ok": True}

    # ------------------------------------------------------- registry/settings
    @r.get("/bookmakers")
    def bookmakers():
        s = settings()
        provider_configured = {p.name: p.configured() for p in c.refresh.providers(s)}
        seen: dict[str, dict] = {}
        for fid, quotes in c.repo.all_quotes().items():
            for q in quotes:
                if q.source in ("manual", "demo"):
                    entry = seen.setdefault(q.bookmaker, {"auto_last": None, "auto_quotes": 0, "manual_quotes": 0})
                    if q.source == "manual":
                        entry["manual_quotes"] += 1
                    continue
                entry = seen.setdefault(q.bookmaker, {"auto_last": None, "auto_quotes": 0, "manual_quotes": 0})
                entry["auto_quotes"] += 1
                if entry["auto_last"] is None or q.fetched_at > entry["auto_last"]:
                    entry["auto_last"] = q.fetched_at
        now = utcnow()
        out = []
        for b in c.registry.bookmakers():
            sources = b.get("sources", {})
            info = seen.get(b["key"], {})
            last = info.get("auto_last")
            if last and now - last < timedelta(hours=24):
                state = "connected"
            elif any(provider_configured.get(src) for src in sources):
                state = "configured_no_data"
            elif sources:
                state = "provider_not_configured"
            else:
                state = "manual_only"
            out.append({"key": b["key"], "name": b["name"], "country": b.get("country", ""), "type": b.get("type", "soft"),
                        "priority": bool(b.get("priority")), "sources": sorted(sources), "state": state,
                        "limitation": b.get("limitation"), "note": b.get("note"),
                        "last_automated_quote": last.isoformat() if last else None,
                        "automated_quotes": info.get("auto_quotes", 0), "manual_quotes": info.get("manual_quotes", 0),
                        "mine": b["key"] in (s.get("my_bookmakers") or [])})
        known = {b["key"] for b in out}
        for key, info in seen.items():
            if key not in known and not key.startswith("demo_"):
                out.append({"key": key, "name": c.registry.bookmaker_name(key), "country": "", "type": "soft", "priority": False,
                            "sources": [], "state": "connected" if info.get("auto_last") else "manual_only", "limitation": None,
                            "note": "Not in the registry (seen in provider data)", "unregistered": True,
                            "last_automated_quote": info["auto_last"].isoformat() if info.get("auto_last") else None,
                            "automated_quotes": info.get("auto_quotes", 0), "manual_quotes": info.get("manual_quotes", 0),
                            "mine": key in (s.get("my_bookmakers") or [])})
        out.sort(key=lambda b: (not b["priority"], b["state"] != "connected", b["name"].lower()))
        return out

    @r.get("/competitions")
    def competitions(sport: Optional[SPORT] = None):
        s = settings()
        out = []
        for comp in c.registry.competitions(sport=sport):
            prov = comp.get("providers", {})
            out.append({"id": comp["id"], "sport": comp["sport"], "name": comp["name"], "country": comp.get("country", ""),
                        "enabled": comp["enabled"], "odds_source": "the_odds_api" if prov.get("the_odds_api") else None,
                        "stats_source": "api_sports" if prov.get("api_sports") else None})
        return {"items": out, "excluded_by_request": ["Danish Superliga", "Spanish La Liga", "Swedish Allsvenskan"],
                "odds_api_cost_per_refresh": sum(1 for x in out if x["enabled"] and x["odds_source"])
                * len(s["odds_api_regions"]) * len(s["odds_api_markets"])}

    @r.put("/competitions/{cid}")
    def toggle_competition(cid: str, body: CompetitionToggle):
        try:
            c.registry.set_enabled(cid, body.enabled)
        except KeyError:
            raise HTTPException(404, "Unknown competition") from None
        return {"ok": True}

    @r.get("/settings")
    def get_settings():
        return c.settings.public()

    @r.put("/settings")
    def put_settings(patch: dict):
        before = settings()["demo_mode"]
        result = c.settings.update(patch)
        after = settings()["demo_mode"]
        if after and not before:
            load_demo(c.repo, c.registry, utcnow())
        elif before and not after:
            c.repo.remove_demo_data()
        return result

    @r.get("/markets/{sport}")
    def markets_for(sport: SPORT):
        return [{"market": m, "selections": list(selections_for(sport, m)), "needs_line": m in LINE_MARKETS} for m in supported_markets(sport)]

    return r
