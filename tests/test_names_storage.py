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
