import pytest

from app.core.markets import market_id, parse_market_id, selection_label, swap_sides
from app.core.models import TeamStats
from app.core.odds_math import devig
from app.sports import model_for
from app.sports.settle import outcome, payout


def test_market_ids_roundtrip():
    assert market_id("totals", 2.5) == "totals@2.5"
    assert market_id("spreads", -1.5) == "spreads@-1.5"
    assert parse_market_id("spreads@-1.5") == ("spreads", -1.5)
    assert parse_market_id("h2h") == ("h2h", None)
    assert selection_label("football", "spreads", -1.5, "away", "A", "B") == "B +1.5"
    assert swap_sides("spreads", -1.5, "home") == (1.5, "away")


def test_football_probabilities_are_consistent():
    m = model_for("football")
    theta = {"lh": 1.6, "la": 1.1}
    p = m.probabilities(theta, "h2h", None)
    assert sum(p.values()) == pytest.approx(1.0)
    assert p["home"] > p["away"]
    tot = m.probabilities(theta, "totals", 2.5)
    assert tot["over"] + tot["under"] == pytest.approx(1.0)
    # handicap -0.5 for home == home win
    sp = m.probabilities(theta, "spreads", -0.5)
    assert sp["home"] == pytest.approx(p["home"], abs=1e-9)
    assert m.probabilities(theta, "totals", 2.25) is None  # quarter lines: market only


def test_football_fit_reproduces_market():
    m = model_for("football")
    market = dict(zip(("home", "draw", "away"), devig([2.10, 3.40, 3.60], "shin")))
    theta, rms = m.fit_market({"h2h": market})
    fitted = m.probabilities(theta, "h2h", None)
    for k in market:
        assert fitted[k] == pytest.approx(market[k], abs=0.01)
    assert rms < 0.01


def test_basketball_has_no_draw_but_handball_does():
    b = model_for("basketball")
    pb = b.probabilities({"mu": 0.0, "total": 160, "sd_m": 11.5, "sd_t": 15}, "h2h", None)
    assert set(pb) == {"home", "away"} and pb["home"] == pytest.approx(0.5)
    h = model_for("handball")
    ph = h.probabilities({"mu": 0.0, "total": 56, "sd_m": 5.8, "sd_t": 6}, "h2h", None)
    assert 0.04 < ph["draw"] < 0.10
    assert sum(ph.values()) == pytest.approx(1.0)


def test_sport_models_differ():
    # identical "1.5 goals/points better" input must not give identical probabilities across sports
    f = model_for("football").probabilities({"lh": 2.0, "la": 1.0}, "h2h", None)["home"]
    hb = model_for("handball").probabilities({"mu": 1.0, "total": 56, "sd_m": 5.8, "sd_t": 6}, "h2h", None)["home"]
    assert abs(f - hb) > 0.05


def test_handball_fit_with_draw_price():
    h = model_for("handball")
    market = dict(zip(("home", "draw", "away"), devig([1.75, 11.0, 2.35], "shin")))
    theta, rms = h.fit_market({"h2h": market})
    assert theta["mu"] > 0
    assert rms < 0.02


def test_from_stats_needs_data():
    m = model_for("football")
    empty = TeamStats(team="x", source="t")
    assert m.from_stats(empty, empty) is None
    good_h = TeamStats(team="h", source="t", games_home=8, games_away=8, scored_home=2.0, conceded_home=0.8,
                       scored_away=1.5, conceded_away=1.2)
    good_a = TeamStats(team="a", source="t", games_home=8, games_away=8, scored_home=1.0, conceded_home=1.4,
                       scored_away=0.8, conceded_away=1.9)
    theta, conf, notes = m.from_stats(good_h, good_a)
    assert theta["lh"] > theta["la"] and 0 < conf <= 0.75


@pytest.mark.parametrize("market,line,sel,h,a,expected", [
    ("h2h", None, "home", 2, 1, "win"),
    ("h2h", None, "draw", 1, 1, "win"),
    ("dnb", None, "home", 1, 1, "push"),
    ("totals", 2.5, "over", 2, 1, "win"),
    ("totals", 3.0, "over", 2, 1, "push"),
    ("totals", 2.25, "over", 2, 0, "half_lose"),
    ("totals", 2.75, "over", 2, 1, "half_win"),
    ("spreads", -1.5, "home", 2, 1, "lose"),
    ("spreads", -1.5, "away", 2, 1, "win"),
    ("btts", None, "yes", 1, 0, "lose"),
])
def test_settlement(market, line, sel, h, a, expected):
    assert outcome(market, line, sel, h, a) == expected


def test_payouts():
    assert payout("win", 2.0) == 2.0
    assert payout("half_win", 2.0) == 1.5
    assert payout("half_lose", 2.0) == 0.5
    assert payout("push", 2.0) == 1.0
