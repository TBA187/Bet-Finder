import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.models import Fixture
from app.core.names import find_fixture, search_score, team_similarity
from app.storage.json_store import JsonStore


@pytest.mark.parametrize("a,b", [
    ("Man Utd", "Manchester United"), ("FC Bayern München", "Bayern Munich"), ("Bayern", "Bayern Munich"),
    ("Paris Saint-Germain", "PSG"), ("LA Lakers", "Los Angeles Lakers"), ("THW Kiel", "Kiel"),
    ("Nott'm Forest", "Nottingham Forest"), ("SG Flensburg-Handewitt", "Flensburg"),
])
def test_same_team_matches(a, b):
    assert team_similarity(a, b) >= 0.78


@pytest.mark.parametrize("a,b", [
    ("Manchester United", "Manchester City"), ("Real Madrid", "Atletico Madrid"),
    ("Sheffield United", "Sheffield Wednesday"), ("Bayer Leverkusen", "Bayern Munich"),
])
def test_different_teams_do_not_match(a, b):
    assert team_similarity(a, b) < 0.78


def test_find_fixture_handles_swaps_and_time():
    ko = datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc)
    fx = Fixture(id="x", sport="football", competition_id="c", competition_name="C", home="Arsenal", away="Chelsea", kickoff=ko)
    hit = find_fixture("football", "Arsenal FC", "Chelsea FC", ko + timedelta(hours=1), [fx])
    assert hit and hit[0].id == "x" and hit[2] is False
    swapped = find_fixture("football", "Chelsea", "Arsenal", ko, [fx])
    assert swapped and swapped[2] is True
    assert find_fixture("football", "Arsenal", "Chelsea", ko + timedelta(days=2), [fx]) is None
    assert find_fixture("basketball", "Arsenal", "Chelsea", ko, [fx]) is None


def test_search_score():
    assert search_score("arsenal chel", "Arsenal", "Chelsea") >= 0.9
    assert search_score("liverpool", "Arsenal", "Chelsea") < 0.5


def test_store_atomic_roundtrip_and_corruption(tmp_path):
    s = JsonStore(tmp_path)
    s.write("things", {"a": 1})
    assert s.read("things", {}) == {"a": 1}
    v = s.version
    (tmp_path / "things.json").write_text("{not json", encoding="utf-8")
    assert s.read("things", {"default": True}) == {"default": True}
    assert any(p.name.startswith("things.json.corrupt-") for p in tmp_path.iterdir())
    with pytest.raises(RuntimeError):
        with s.edit("things", {}) as d:
            d["x"] = 1
            raise RuntimeError("boom")
    assert s.read("things", {}) == {}  # nothing written on failure
    with s.edit("things", {}) as d:
        d["ok"] = True
    assert s.read("things", {}) == {"ok": True} and s.version > v
    assert not list(tmp_path.glob("*.tmp"))


def test_store_rejects_path_traversal(tmp_path):
    s = JsonStore(tmp_path)
    with pytest.raises(ValueError):
        s.write("../evil", {})


def _fake_upstash():
    """In-memory stand-in for the Upstash REST API."""
    import httpx
    db = {}

    def handler(request):
        cmd = json.loads(request.content)
        op = cmd[0].upper()
        if op == "SET":
            db[cmd[1]] = cmd[2]
            return httpx.Response(200, json={"result": "OK"})
        if op == "GET":
            return httpx.Response(200, json={"result": db.get(cmd[1])})
        if op == "DEL":
            n = sum(1 for k in cmd[1:] if db.pop(k, None) is not None)
            return httpx.Response(200, json={"result": n})
        if op == "EXISTS":
            return httpx.Response(200, json={"result": int(cmd[1] in db)})
        if op == "SCAN":
            pattern = cmd[3].rstrip("*")
            return httpx.Response(200, json={"result": ["0", [k for k in db if k.startswith(pattern)]]})
        return httpx.Response(400, json={"error": "unknown"})
    return db, httpx.Client(transport=httpx.MockTransport(handler))


def test_redis_store_roundtrip():
    from app.storage.json_store import RedisStore
    db, client = _fake_upstash()
    s = RedisStore("https://fake.upstash.io", "tok")
    s._client = client
    s.write("odds/fx-1", {"a": 1})
    s.write("odds/fx-2", {"b": 2})
    assert all(v.startswith("z:") for v in db.values())  # stored compressed
    s._cache.clear()
    assert s.read("odds/fx-1", {}) == {"a": 1}
    with s.edit("odds/fx-1", {}) as d:
        d["c"] = 3
    s._cache.clear()
    assert s.read("odds/fx-1", {}) == {"a": 1, "c": 3}
    assert s.list("odds/") == ["odds/fx-1", "odds/fx-2"]
    s.delete("odds/fx-2")
    assert not s.exists("odds/fx-2") and s.read("missing", {"d": 1}) == {"d": 1}


def test_repository_works_on_redis(monkeypatch):
    from app.services.repository import Repository
    from app.storage.json_store import RedisStore
    from tests.conftest import NOW, add_prices, make_fixture
    _db, client = _fake_upstash()
    s = RedisStore("https://fake.upstash.io", "tok")
    s._client = client
    repo = Repository(s)
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 2.0, "draw": 3.4, "away": 3.9})
    assert len(repo.quotes(fx.id)) == 3 and fx.id in repo.all_quotes()


def test_redis_url_store_and_selection(monkeypatch, tmp_path):
    """Standard Redis (REDIS_URL / STORAGE_URL, e.g. Redis Cloud) works like Upstash."""
    from app.storage import json_store
    from app.storage.json_store import RedisURLStore, make_store

    class FakeRedis:
        def __init__(self):
            self.db = {}

        def execute_command(self, *args):
            op = args[0].upper()
            if op == "SET":
                self.db[args[1]] = args[2]
                return True
            if op == "GET":
                return self.db.get(args[1])
            if op == "DEL":
                return sum(1 for k in args[1:] if self.db.pop(k, None) is not None)
            if op == "EXISTS":
                return int(args[1] in self.db)
            if op == "SCAN":
                return (0, [k for k in self.db if k.startswith(args[3].rstrip("*"))])
            raise AssertionError(op)

    fake = FakeRedis()
    import redis
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url, **kw: fake))
    for var in ("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL", "REDIS_URL", "KV_URL", "VERCEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("STORAGE_URL", "rediss://default:pw@example.redis.cloud:12345")
    store = make_store(tmp_path)
    assert isinstance(store, RedisURLStore)
    store.write("settings", {"a": 1})
    store._cache.clear()
    assert store.read("settings", {}) == {"a": 1} and store.list("sett") == ["settings"]
    monkeypatch.delenv("STORAGE_URL")
    assert isinstance(make_store(tmp_path), json_store.JsonStore)


def test_settings_from_environment(monkeypatch, tmp_path):
    from app.config import SettingsService
    monkeypatch.setenv("BETFINDER_SCRAPER_MODE", "always")
    monkeypatch.setenv("BETFINDER_MY_BOOKMAKERS", "danske_spil, nordicbet")
    monkeypatch.setenv("BETFINDER_MIN_EV_PCT", "not-a-number")  # invalid -> ignored, default kept
    svc = SettingsService(JsonStore(tmp_path))
    s = svc.get()
    assert s["scraper_mode"] == "always" and s["my_bookmakers"] == ["danske_spil", "nordicbet"] and s["min_ev_pct"] == 1.5
    svc.update({"scraper_mode": "off"})  # a choice made in the app wins
    assert svc.get()["scraper_mode"] == "off"
