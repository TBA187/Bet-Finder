from datetime import datetime, timedelta, timezone

import pytest

from app.core.models import utcnow
from app.providers.api_sports import ApiSportsProvider, _response
from app.providers.http import ProviderError
from app.providers.the_odds_api import TheOddsApiProvider
from tests.conftest import NOW


def test_status_is_honest_without_sources(client):
    s = client.get("/api/status").json()
    assert not s["any_live_source"]
    assert all(not p["configured"] for p in s["providers"])
    assert client.get("/api/scanner").json()["items"] == []
    picks = client.get("/api/daily-picks").json()
    assert picks["message"] == "No qualifying value bets found today."
    r = client.post("/api/refresh").json()
    assert "No live data source" in r["message"]


def test_bookmaker_coverage_marks_manual_only(client):
    books = {b["key"]: b for b in client.get("/api/bookmakers").json()}
    # only reachable through the paid odds-api.io feed -> "needs API key", with an explanation
    assert books["danske_spil"]["state"] == "provider_not_configured" and books["danske_spil"]["limitation"]
    assert books["eurobet"]["state"] == "provider_not_configured"
    assert books["bet25"]["state"] == "provider_not_configured"
    assert books["nordicbet"]["state"] == "provider_not_configured"
    assert books["danske_spil"]["priority"] and books["nordicbet"]["priority"]


def test_all_requested_leagues_and_sports_present(client):
    items = client.get("/api/competitions").json()["items"]
    by_id = {c["id"]: c for c in items}
    for cid in ("football.denmark_superliga", "football.la_liga", "football.sweden_allsvenskan", "handball.denmark_herreligaen",
                "basketball.denmark_basketligaen", "mma.ufc", "boxing.boxing", "tennis.atp", "icehockey.nhl"):
        assert cid in by_id, cid
    assert by_id["football.la_liga"]["enabled"] and by_id["football.denmark_superliga"]["enabled"]
    assert {c["category"] for c in items} == {"team", "combat", "racket"}
    cat = client.get("/api/catalog").json()
    assert set(cat["categories"]) == {"team", "combat", "racket"} and "mma" in cat["sports"]


def test_live_only_no_demo(client):
    s = client.get("/api/settings").json()
    assert "demo_mode" not in s
    assert client.put("/api/settings", json={"demo_mode": True}).status_code == 400
    st = client.get("/api/status").json()
    assert st["storage"]["kind"] == "files" and st["storage"]["persistent"]
    # real (manually entered) prices flow through scanner, analysis and picks
    ko = (utcnow() + timedelta(hours=5)).isoformat()
    for book, prices in (("pinnacle", {"home": 2.0, "draw": 3.5, "away": 3.9}), ("nordicbet", {"home": 2.3, "draw": 3.3, "away": 3.6})):
        client.post("/api/manual/odds", json={"sport": "football", "home": "Live A", "away": "Live B", "kickoff": ko,
                                              "bookmaker": book, "market": "h2h", "prices": prices})
    items = client.get("/api/scanner").json()["items"]
    assert items and not any(i["fixture"]["is_demo"] for i in items)
    a = client.get(f"/api/fixtures/{items[0]['fixture']['id']}").json()
    assert {d["status"] for d in a["data_status"] if d["kind"] == "odds"} == {"manual"}


def test_old_demo_data_is_purged(paths):
    from app.core.models import Fixture
    from app.main import create_app
    from app.services.repository import Repository
    from app.storage.json_store import JsonStore
    repo = Repository(JsonStore(paths.runtime))
    repo.save_fixture(Fixture(id="old-demo", sport="football", competition_id="demo.football", competition_name="Demo",
                              home="Demo A", away="Demo B", kickoff=utcnow() + timedelta(hours=2), is_demo=True))
    repo.store.write("settings", {"demo_mode": True})
    create_app(paths, background=False)
    assert repo.fixture("old-demo") is None and "demo_mode" not in repo.store.read("settings", {})


def test_cron_endpoint_auth(paths, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    monkeypatch.setenv("CRON_SECRET", "cron-123")
    with TestClient(create_app(paths, background=False)) as c:
        c.put("/api/settings", json={"scraper_mode": "off"})
        assert c.get("/api/cron/refresh").status_code == 401
        assert c.get("/api/cron/refresh", headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = c.get("/api/cron/refresh", headers={"Authorization": "Bearer cron-123"})
        assert r.status_code == 200 and r.json()["odds_methods"] == []


def test_manual_entry_creates_then_matches_fixture(client):
    ko = (utcnow() + timedelta(hours=6)).isoformat()
    body = {"sport": "football", "home": "Arsenal", "away": "Chelsea", "competition_id": "football.epl", "kickoff": ko,
            "bookmaker": "Danske Spil", "market": "h2h", "prices": {"home": 2.10, "draw": 3.40, "away": 3.30}}
    r = client.post("/api/manual/odds", json=body)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["matched_by"] == "new match created" and first["bookmaker"] == "danske_spil"
    assert first["margin_pct"] == pytest.approx((1 / 2.1 + 1 / 3.4 + 1 / 3.3 - 1) * 100, abs=0.05)
    # second bookmaker: only team names, no kick-off -> must find the same match
    body2 = {"sport": "football", "home": "Arsenal FC", "away": "Chelsea FC", "bookmaker": "Eurobet", "market": "h2h",
             "prices": {"home": 2.25, "draw": 3.30, "away": 3.20}}
    second = client.post("/api/manual/odds", json=body2).json()
    assert second["fixture"]["id"] == first["fixture"]["id"]
    assert second["matched_by"].startswith("matched by team names")
    assert len(second["market"]["books"]) == 2
    found = client.get("/api/fixtures/search", params={"q": "arsen chel"}).json()
    assert found and found[0]["id"] == first["fixture"]["id"]


def test_manual_entry_validation(client):
    ko = (utcnow() + timedelta(hours=6)).isoformat()
    base = {"sport": "football", "home": "A", "away": "B", "kickoff": ko, "bookmaker": "x", "market": "h2h"}
    assert client.post("/api/manual/odds", json={**base, "prices": {"home": 0.9}}).status_code == 422
    assert client.post("/api/manual/odds", json={**base, "prices": {"winner": 2.0}}).status_code == 400
    assert client.post("/api/manual/odds", json={**base, "market": "totals", "prices": {"over": 1.9}}).status_code == 400
    past = {**base, "kickoff": (utcnow() - timedelta(hours=1)).isoformat(), "prices": {"home": 2.0}}
    assert client.post("/api/manual/odds", json=past).status_code == 400


def test_tracker_settles_and_computes_clv(client):
    ko = (utcnow() + timedelta(hours=3)).isoformat()
    for book, prices in (("pinnacle", {"home": 2.0, "draw": 3.5, "away": 3.9}), ("nordicbet", {"home": 2.2, "draw": 3.3, "away": 3.6})):
        r = client.post("/api/manual/odds", json={"sport": "football", "home": "Gamma", "away": "Delta", "kickoff": ko,
                                                   "bookmaker": book, "market": "h2h", "prices": prices})
        assert r.status_code == 200
    fid = r.json()["fixture"]["id"]
    t = client.post("/api/tracker/selection", json={"fixture_id": fid, "market_id": "h2h", "selection": "home", "stake": 10})
    assert t.status_code == 200, t.text
    item = t.json()
    assert item["legs"][0]["bookmaker"] == "nordicbet" and item["legs"][0]["odds"] == 2.2
    # simulate kick-off having passed and a final score arriving
    c = client.app.state.container
    fx = c.repo.fixture(fid)
    fx.kickoff = utcnow()  # prices were stored just before "kick-off"
    c.repo.save_fixture(fx)
    assert client.post(f"/api/fixtures/{fid}/result", json={"score_home": 2, "score_away": 0}).status_code == 200
    ov = client.get("/api/tracker").json()
    tracked = ov["items"][0]
    assert tracked["status"] == "won" and tracked["profit"] == pytest.approx(12.0)
    assert tracked["legs"][0]["clv_pct"] is not None
    assert ov["summary"]["settled"] == 1 and ov["summary"]["roi_pct"] == pytest.approx(120.0)


def test_settings_validation(client):
    assert client.put("/api/settings", json={"min_ev_pct": -5}).status_code == 400
    assert client.put("/api/settings", json={"nope": 1}).status_code == 400
    assert client.put("/api/settings", json={"max_risk": "extreme"}).status_code == 400
    r = client.put("/api/settings", json={"odds_api_key": "abcdefghijklmnop"}).json()
    assert r["odds_api_key"]["configured"] and "abcdefgh" not in str(r)  # key never echoed back


def test_token_guard(paths, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    monkeypatch.setenv("BETFINDER_TOKEN", "s3cret")
    with TestClient(create_app(paths, background=False)) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/status", headers={"X-BetFinder-Token": "s3cret"}).status_code == 200


# ----------------------------------------------------------------- providers
ODDS_API_EVENT = {
    "id": "ev1", "sport_key": "soccer_epl", "commence_time": "2026-11-08T17:30:00Z",
    "home_team": "Arsenal", "away_team": "Chelsea",
    "bookmakers": [
        {"key": "nordicbet", "title": "NordicBet", "markets": [
            {"key": "h2h", "last_update": "2026-11-08T11:55:00Z", "outcomes": [
                {"name": "Arsenal", "price": 2.05}, {"name": "Chelsea", "price": 3.6}, {"name": "Draw", "price": 3.5}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9, "point": 2.5}, {"name": "Under", "price": 1.95, "point": 2.5}]}]},
        {"key": "unibet_se", "title": "Unibet (SE)", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Arsenal", "price": 2.0}, {"name": "Chelsea", "price": 3.5}, {"name": "Draw", "price": 3.4}]}]},
        {"key": "unibet_nl", "title": "Unibet (NL)", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Arsenal", "price": 1.5}, {"name": "Chelsea", "price": 9.0}, {"name": "Draw", "price": 4.0}]}]},
        {"key": "newbook", "title": "New Book", "markets": [{"key": "spreads", "outcomes": [
            {"name": "Arsenal", "price": 1.9, "point": -0.5}, {"name": "Chelsea", "price": 1.9, "point": 0.5}]}]},
    ],
}


def test_odds_api_ingest(repo, registry, settings):
    p = TheOddsApiProvider(repo, registry, {**settings, "odds_api_key": "k"})
    n = p._ingest(ODDS_API_EVENT, registry.competition("football.epl"), NOW)
    assert n == 3 + 2 + 3 + 2
    fx = repo.fixtures()[0]
    quotes = repo.quotes(fx.id)
    unibet = {q.selection: q.odds for q in quotes if q.bookmaker == "unibet" and q.market == "h2h"}
    assert unibet == {"home": 2.0, "away": 3.5, "draw": 3.4}  # preferred unibet_se feed wins over unibet_nl
    spreads = {q.selection: q.line for q in quotes if q.market == "spreads"}
    assert spreads == {"home": -0.5, "away": -0.5}  # away +0.5 == home line -0.5
    # past events (live) are ignored
    assert p._ingest({**ODDS_API_EVENT, "id": "ev2", "commence_time": "2026-11-08T10:00:00Z"},
                     registry.competition("football.epl"), NOW) is None


def test_fixtures_from_two_providers_merge(repo, registry, settings):
    TheOddsApiProvider(repo, registry, settings)._ingest(ODDS_API_EVENT, registry.competition("football.epl"), NOW)
    from app.core.models import Fixture
    cand = Fixture(id="", sport="football", competition_id="football.epl", competition_name="Premier League",
                   home="Arsenal FC", away="Chelsea FC", kickoff=datetime(2026, 11, 8, 17, 30, tzinfo=timezone.utc),
                   team_refs={"api_sports": {"home": "42", "away": "49"}})
    fx, swapped = repo.merge_fixture(cand, "api_sports", "999")
    assert len(repo.fixtures()) == 1 and not swapped
    assert fx.refs == {"the_odds_api": "ev1", "api_sports": "999"}


def test_api_sports_parsing(repo, registry, settings):
    p = ApiSportsProvider(repo, registry, settings)
    assert p._parse_value("h2h", "Home") == (None, "home")
    assert p._parse_value("totals", "Over 2.5") == (2.5, "over")
    assert p._parse_value("spreads", "Away +1.5") == (-1.5, "away")
    assert p._parse_value("btts", "Yes") == (None, "yes")
    assert p._parse_value("totals", "garbage") is None
    with pytest.raises(ProviderError, match="plan"):
        _response({"errors": {"plan": "Free plans do not have access to this season"}, "response": []})
    assert _response({"errors": [], "response": [1]}) == [1]
    season = ApiSportsProvider._current_season([{"year": 2024, "current": False}, {"year": 2025, "current": True}], "football")
    assert season["value"] == 2025
