"""Tests for the v1.2 features: new sports, Danish, fallback chain, scraper
politeness, extra providers, bet builder, arbitrage, notifications, usage."""
import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from app.core.models import utcnow
from app.core.odds_math import devig
from app.providers.common import match_competition
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.oddspapi import OddsPapiProvider
from app.providers.scraper import PoliteFetcher, ScrapeBlocked, ScraperProvider
from app.services import insights, notify
from app.services.builder import evaluate
from app.services.refresh import RefreshService
from app.sports import model_for
from tests.conftest import NOW, add_prices, make_fixture


# ------------------------------------------------------------------ sports
def test_new_sport_models():
    hockey = model_for("icehockey")
    reg = hockey.probabilities({"lh": 3.2, "la": 2.8}, "h2h3", None)
    ml = hockey.probabilities({"lh": 3.2, "la": 2.8}, "h2h", None)
    assert sum(reg.values()) == pytest.approx(1) and set(ml) == {"home", "away"}
    assert ml["home"] > reg["home"]  # overtime wins are added to the moneyline
    mma = model_for("mma")
    theta, _ = mma.fit_market({"h2h": dict(zip(("home", "away"), devig([1.55, 2.55])))})
    assert mma.probabilities(theta, "h2h", None)["home"] > 0.55
    boxing = model_for("boxing")
    theta, _ = boxing.fit_market({"h2h3": dict(zip(("home", "draw", "away"), devig([1.6, 21.0, 2.6])))})
    assert 0.02 < boxing.probabilities(theta, "h2h3", None)["draw"] < 0.08
    nfl = model_for("americanfootball")
    assert nfl.probabilities({"mu": 3.0, "total": 45, "sd_m": 13.5, "sd_t": 13}, "h2h", None)["home"] > 0.55


def test_individual_sport_analysis_is_market_based(repo, analysis, settings):
    fx = make_fixture(repo, sport="mma", home="Fighter A", away="Fighter B", competition_id="mma.ufc", competition_name="UFC")
    add_prices(repo, fx, "pinnacle", {"home": 1.60, "away": 2.45})
    add_prices(repo, fx, "betsson", {"home": 1.55, "away": 2.50})
    add_prices(repo, fx, "nordicbet", {"home": 1.57, "away": 2.75})
    a = analysis.analyze(fx, settings, NOW)
    ha = next(ag for ag in a["agents"] if ag["agent"] == "home_away")
    assert ha["status"] == "no_data" and "home advantage" in ha["summary"]
    away = next(s for s in a["selections"] if s["selection"] == "away")
    assert away["best"]["bookmaker"] == "nordicbet" and away["ev_pct"] > 0


# ------------------------------------------------------------------ danish
def test_danish_texts(repo, analysis, settings):
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 2.05, "draw": 3.50, "away": 3.70})
    a = analysis.analyze(fx, {**settings, "language": "da"}, NOW)
    assert a["agents"][0]["name"] == "Oddsscanner-agent"
    assert a["markets"][0]["label"] == "Kampresultat 1X2"
    assert any(s["label"] == "Uafgjort" for s in a["selections"])


def test_danish_no_picks_message(client):
    client.put("/api/settings", json={"language": "da"})
    assert client.get("/api/daily-picks").json()["message"] == "Ingen kvalificerede værdispil fundet i dag."


# ------------------------------------------------------------------ builder / arbitrage
def test_builder_models_same_match_correlation(repo, analysis, settings, registry):
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 1.60, "draw": 4.20, "away": 5.50})
    add_prices(repo, fx, "pinnacle", {"over": 1.80, "under": 2.05}, market="totals", line=2.5)
    a = analysis.analyze(fx, settings, NOW)
    comps = {c["id"]: c for c in registry.competitions()}
    r = evaluate([{"fixture_id": fx.id, "market_id": "h2h", "selection": "home"},
                  {"fixture_id": fx.id, "market_id": "totals@2.5", "selection": "over"}], {fx.id: a}, comps, None)
    g = r["groups"][0]
    assert g["modelled"] and g["correlation"] > 1.05  # home win and many goals go together
    assert r["probability"] > g["independent_probability"]
    # contradictory legs have zero joint probability
    r2 = evaluate([{"fixture_id": fx.id, "market_id": "h2h", "selection": "home"},
                   {"fixture_id": fx.id, "market_id": "h2h", "selection": "away"}], {fx.id: a}, comps, 10.0)
    assert r2["probability"] == pytest.approx(0, abs=1e-6)


def test_arbitrage_stakes_equalise_returns(repo, analysis, settings):
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 2.10, "draw": 3.40, "away": 3.60})
    add_prices(repo, fx, "nordicbet", {"home": 2.40, "draw": 3.60, "away": 4.10})
    a = analysis.analyze(fx, settings, NOW)
    arbs = insights.arbitrage([a], stake=100)
    assert arbs and arbs[0]["profit_pct"] > 0
    legs = arbs[0]["legs"]
    assert sum(l["stake"] for l in legs) == pytest.approx(100, abs=0.05)
    returns = [l["stake"] * l["odds"] for l in legs]
    assert max(returns) - min(returns) < 0.5


# ------------------------------------------------------------------ providers
def test_odds_api_io_parsing():
    items = OddsApiIoProvider.parse_market({"name": "ML", "odds": [{"home": "2.10", "draw": "3.40", "away": "3.20"}]}, "football")
    assert ("h2h", None, "draw", 3.4) in items
    items = OddsApiIoProvider.parse_market({"name": "Totals", "odds": [{"max": 2.5, "over": "1.90", "under": "1.95"}]}, "football")
    assert ("totals", 2.5, "over", 1.9) in items
    items = OddsApiIoProvider.parse_market({"name": "ML", "odds": [{"home": "1.50", "away": "2.60"}]}, "basketball")
    assert {x[0] for x in items} == {"h2h"}


def test_oddspapi_parsing():
    mk = {"101": {"outcomes": {"101": {"players": {"0": {"price": 2.1, "active": True}}},
                               "102": {"players": {"0": {"price": 3.3, "active": True}}},
                               "103": {"players": {"0": {"price": 3.6, "active": False}}}}}}
    items = OddsPapiProvider.parse_markets(mk, "football")
    assert ("h2h", None, "home", 2.1) in items and not any(x[2] == "away" for x in items)  # inactive price dropped


def test_league_matching_is_strict(registry):
    assert match_competition(registry, "football", "La Liga", country="Spain")["id"] == "football.la_liga"
    assert match_competition(registry, "football", "La Liga 2", country="Spain") is None
    assert match_competition(registry, "football", "Super League 2", country="Greece") is None
    assert match_competition(registry, "football", "Premier League", country="Egypt") is None
    assert match_competition(registry, "football", "Superliga", country="Denmark")["id"] == "football.denmark_superliga"


def _kambi_event(home, away, start, path):
    return {"event": {"id": 1, "homeName": home, "awayName": away, "start": start, "state": "NOT_STARTED",
                      "group": path[-1], "path": [{"englishName": p} for p in path]},
            "betOffers": [{"betOfferType": {"englishName": "Match"}, "criterion": {"englishLabel": "Full Time"},
                           "outcomes": [{"type": "OT_ONE", "odds": 2100}, {"type": "OT_CROSS", "odds": 3400}, {"type": "OT_TWO", "odds": 3500}]},
                          {"betOfferType": {"englishName": "Over/Under"}, "criterion": {"englishLabel": "Total Goals"},
                           "outcomes": [{"type": "OT_OVER", "odds": 1900, "line": 2500}, {"type": "OT_UNDER", "odds": 1900, "line": 2500}]}]}


def test_scraper_parses_kambi_and_creates_fixture(repo, registry, settings):
    prov = ScraperProvider(repo, registry, settings)
    start = (utcnow() + timedelta(hours=5)).isoformat()
    n = prov._kambi_event(_kambi_event("Brøndby IF", "FC København", start, ["Football", "Denmark", "Superliga"]),
                          "football", "unibet", [], utcnow())
    assert n == 5
    fx = repo.fixtures()[0]
    assert fx.competition_id == "football.denmark_superliga"
    odds = {(q.market_id, q.selection): q.odds for q in repo.quotes(fx.id)}
    assert odds[("h2h", "home")] == 2.1 and odds[("totals@2.5", "over")] == 1.9
    assert all(q.source == "scraper" for q in repo.quotes(fx.id))
    # a league that is not enabled/known is ignored, not guessed
    assert prov._kambi_event(_kambi_event("A", "B", start, ["Football", "Greece", "Super League 2"]), "football", "unibet", [], utcnow()) == 0


def test_polite_fetcher_respects_robots_and_blocks(repo):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        if request.url.path == "/blocked.json":
            return httpx.Response(403)
        return httpx.Response(200, json={"ok": True})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            f = PoliteFetcher(repo, client, min_interval=0)
            assert await f.get_json("https://example.test/data.json", {}) == {"ok": True}
            with pytest.raises(ScrapeBlocked, match="robots"):
                await f.get_json("https://example.test/private/x.json", {})
            with pytest.raises(ScrapeBlocked, match="403"):
                await f.get_json("https://example.test/blocked.json", {})
            with pytest.raises(ScrapeBlocked, match="paused"):  # host now paused: no further requests
                await f.get_json("https://example.test/data.json", {})
    asyncio.run(run())
    assert "/private/x.json" not in calls  # disallowed page never requested
    assert calls.count("/robots.txt") == 1
    assert "example.test" in repo.provider_status()["scraper"]["blocked"]


def test_fallback_chain(repo, registry, store, monkeypatch):
    from app.config import SettingsService
    svc = SettingsService(store)
    from app.services.tracker import TrackerService
    rs = RefreshService(repo, registry, svc, TrackerService(repo, registry))
    ran = []

    async def fake_scraper(self, client, competitions, now):
        ran.append("scraper")
        return {"provider": "scraper", "quotes": 3, "fixtures": 1, "errors": []}
    monkeypatch.setattr(ScraperProvider, "refresh", fake_scraper)
    r = asyncio.run(rs.run(NOW))  # no API keys -> scraping fallback
    assert ran == ["scraper"] and r["odds_methods"] == ["scraping"]
    assert {c["provider"]: c["reason"] for c in r["chain"]}["the_odds_api"] == "not_configured"
    svc.update({"scraper_mode": "off"})
    ran.clear()
    r = asyncio.run(rs.run(NOW))
    assert ran == [] and r["message"]


# ------------------------------------------------------------------ notifications / usage
def test_notifications_are_sent_once(repo, analysis, settings):
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 2.05, "draw": 3.50, "away": 3.70})
    add_prices(repo, fx, "betsson", {"home": 1.98, "draw": 3.40, "away": 3.60})
    add_prices(repo, fx, "nordicbet", {"home": 1.97, "draw": 3.35, "away": 4.60})
    s = {**settings, "notify_ntfy_topic": "bf-test-topic", "notify_ntfy_server": "https://ntfy.example"}
    a = analysis.analyze(fx, s, NOW)
    sent = []

    def handler(request):
        sent.append(request.content.decode())
        return httpx.Response(200)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await notify.process(client, repo, s, [a], None)
            second = await notify.process(client, repo, s, [a], None)
        return first, second
    first, second = asyncio.run(run())
    assert first["sent"] >= 1 and first["channels"] == ["ntfy: ok"]
    assert second["sent"] == 0 and len(sent) == 1
    assert "Beta United" in sent[0]


def test_usage_and_credit_plan(client):
    u = client.get("/api/usage").json()
    names = {p["provider"]: p for p in u["providers"]}
    assert names["scraper"]["method"] == "scraping" and names["the_odds_api"]["method"] == "api"
    assert names["oddspapi"]["tokens"]["limit"] == 250
    plan = client.get("/api/credits-plan").json()
    assert plan["monthly_credits"] > 0 and plan["recommended_plan"]


def test_three_coupons_default_and_manual_multi(client):
    assert client.get("/api/settings").json()["max_coupons"] == 3
    ko = (utcnow() + timedelta(hours=4)).isoformat()
    body = {"sport": "football", "home": "Aalborg", "away": "Odense", "competition_id": "football.denmark_superliga",
            "kickoff": ko, "market": "h2h", "rows": [{"bookmaker": "Danske Spil", "prices": {"home": 2.1, "draw": 3.4, "away": 3.3}},
                                                     {"bookmaker": "NordicBet", "prices": {"home": 2.2, "draw": 3.3, "away": 3.2}}]}
    r = client.post("/api/manual/odds-multi", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["bookmaker_names"] == ["Danske Spil", "NordicBet"]
    assert len(r.json()["market"]["books"]) == 2


def test_manual_xg_feeds_the_model(client):
    ko = (utcnow() + timedelta(hours=4)).isoformat()
    r = client.post("/api/manual/odds", json={"sport": "football", "home": "Xg Home", "away": "Xg Away", "kickoff": ko,
                                              "bookmaker": "pinnacle", "market": "h2h", "prices": {"home": 2.5, "draw": 3.3, "away": 2.9}}).json()
    fid = r["fixture"]["id"]
    assert client.put(f"/api/fixtures/{fid}/xg/home", json={"xg_for": 2.4, "xg_against": 0.7}).status_code == 200
    assert client.put(f"/api/fixtures/{fid}/xg/away", json={"xg_for": 0.8, "xg_against": 2.1}).status_code == 200
    a = client.get(f"/api/fixtures/{fid}").json()
    st = next(ag for ag in a["agents"] if ag["agent"] == "statistics")
    assert any("xG" in f for f in st["findings"]) and st["lean_home"] > 0
    assert any(d["kind"] == "statistics" and d["status"] == "manual" for d in a["data_status"])


# ------------------------------------------------------------------ v1.3 robustness fixes
def test_api_sports_free_plan_stops_early_and_pauses(repo, registry, settings):
    from app.providers.api_sports import ApiSportsProvider
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"errors": {"plan": "Free plans do not have access to this season, try from 2022 to 2024."},
                                         "response": []})
    comps = [c for c in registry.competitions(sport="football", enabled_only=True)]
    assert len(comps) > 5
    prov = ApiSportsProvider(repo, registry, {**settings, "api_sports_key": "k"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await prov.refresh(client, comps, utcnow())
            second = await prov.refresh(client, comps, utcnow())
        return first, second
    first, second = asyncio.run(run())
    assert len(first["errors"]) == 1 and len(calls) == 1  # one failing call, not one per league
    assert any("paused" in s for s in second["skipped"]) and len(calls) == 1
    u = insights.usage(repo.provider_status(), settings, [prov], lambda k: "settings", utcnow())
    assert u["providers"][0]["state"] == "plan_limited" and u["providers"][0]["hint"] == "free_plan_seasons"


def test_rejected_key_stops_odds_api_io(repo, registry, settings):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/sports"):
            return httpx.Response(200, json=[{"name": "Football", "slug": "football"}, {"name": "Basketball", "slug": "basketball"}])
        return httpx.Response(401, json={"error": "invalid key"})
    make_fixture(repo, hours=5, now=utcnow())
    make_fixture(repo, sport="basketball", home="Hoop A", away="Hoop B", hours=5, now=utcnow(), competition_id="basketball.nba",
                 competition_name="NBA")
    prov = OddsApiIoProvider(repo, registry, {**settings, "odds_api_io_key": "wrong"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await prov.refresh(client, [], utcnow())
    r = asyncio.run(run())
    assert len(r["errors"]) == 1 and "rejected" in r["errors"][0]
    assert calls.count("/v3/events") == 1  # stopped after the first rejection
    u = insights.usage(repo.provider_status(), settings, [prov], lambda k: "settings", utcnow())
    assert u["providers"][0]["state"] == "error" and u["providers"][0]["hint"] == "key_rejected"


def test_daily_window_after_midnight_covers_the_next_day(settings):
    from datetime import datetime, timezone
    from app.services.daily_picks import day_window
    _, end, day = day_window(datetime(2026, 11, 9, 1, 21, tzinfo=timezone.utc), settings)  # 02:21 in Copenhagen
    assert day == "2026-11-09" and end == datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc)
    _, end, _ = day_window(datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc), settings)  # 19:00
    assert end == datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc)


def test_provider_key_test_endpoint_without_key(client):
    r = client.post("/api/providers/the_odds_api/test").json()
    assert r == {"ok": False, "code": "no_key"}
