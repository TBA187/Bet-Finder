from datetime import timedelta

import pytest

from app.agents.assessment import FormAgent
from app.core.models import Absence, TeamStats
from app.services.analysis import AGENT_ORDER
from app.services.daily_picks import NO_PICKS_MESSAGE, build_daily_picks
from tests.conftest import NOW, add_prices, make_fixture


def _sel(a, label):
    return next(s for s in a["selections"] if s["label"] == label)


def test_ten_agents_and_market_only_estimate(repo, analysis, settings):
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 2.05, "draw": 3.50, "away": 3.70})
    add_prices(repo, fx, "nordicbet", {"home": 1.98, "draw": 3.40, "away": 3.60})
    add_prices(repo, fx, "betsson", {"home": 2.00, "draw": 3.35, "away": 3.55})
    a = analysis.analyze(fx, settings, NOW)
    assert [ag["agent"] for ag in a["agents"]] == AGENT_ORDER and len(AGENT_ORDER) == 10
    # No stats / injuries / lineups -> those agents report no_data instead of inventing anything
    by = {ag["agent"]: ag for ag in a["agents"]}
    for key in ("probability", "form", "home_away", "injuries", "suspensions", "lineups", "statistics"):
        assert by[key]["status"] == "no_data" and by[key]["confidence"] == 0
    # with no extra information the estimate equals the bookmaker consensus
    for s in a["selections"]:
        assert s["final_prob"] == pytest.approx(s["market_prob"], abs=1e-4)
    assert a["confidence"] < 60  # missing data lowers confidence


def test_single_bookmaker_never_shows_value(repo, analysis, settings):
    fx = make_fixture(repo)
    add_prices(repo, fx, "danske_spil", {"home": 2.10, "draw": 3.30, "away": 3.40}, source="manual")
    a = analysis.analyze(fx, settings, NOW)
    assert all(s["ev_pct"] < 0 for s in a["selections"])  # own margin only -> no value
    assert not any(s["qualifies"] for s in a["selections"])


def test_outlier_price_is_detected_as_value(repo, analysis, settings):
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 2.05, "draw": 3.50, "away": 3.70})
    add_prices(repo, fx, "betsson", {"home": 1.98, "draw": 3.40, "away": 3.60})
    add_prices(repo, fx, "unibet", {"home": 2.00, "draw": 3.40, "away": 3.55})
    add_prices(repo, fx, "nordicbet", {"home": 1.97, "draw": 3.35, "away": 4.40})  # mispriced away
    a = analysis.analyze(fx, settings, NOW)
    s = _sel(a, "Beta United")
    assert s["best"]["bookmaker"] == "nordicbet"
    assert s["ev_pct"] > 10 and s["market_ev_pct"] > 10
    assert "possible_pricing_error" in s["pricing_flags"] or "outlier_price" in s["pricing_flags"]
    assert s["risk_rating"] in ("medium", "high", "very_high")  # palpable-error risk is flagged


def test_high_odds_with_poor_probability_do_not_qualify(repo, analysis, settings):
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 1.25, "draw": 6.50, "away": 12.0})
    add_prices(repo, fx, "betsson", {"home": 1.22, "draw": 6.20, "away": 11.0})
    add_prices(repo, fx, "unibet", {"home": 1.24, "draw": 6.00, "away": 11.5})
    a = analysis.analyze(fx, settings, NOW)
    away = _sel(a, "Beta United")
    assert away["best"]["odds"] == 12.0 and not away["qualifies"]


def test_agent_crash_is_contained(repo, analysis, settings, monkeypatch):
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 2.05, "draw": 3.50, "away": 3.70})

    def boom(self, ctx):
        raise RuntimeError("bad data")
    monkeypatch.setattr(FormAgent, "run", boom)
    a = analysis.analyze(fx, settings, NOW)
    form = next(ag for ag in a["agents"] if ag["agent"] == "form")
    assert form["status"] == "error" and form["confidence"] == 0
    assert a["selections"]


def test_injuries_shift_probability_and_agents_can_disagree(repo, analysis, settings):
    fx = make_fixture(repo)
    add_prices(repo, fx, "pinnacle", {"home": 2.40, "draw": 3.40, "away": 3.00})
    add_prices(repo, fx, "betsson", {"home": 2.35, "draw": 3.30, "away": 2.95})
    base = analysis.analyze(fx, settings, NOW)
    p0 = _sel(base, "Alpha FC")["final_prob"]
    for i in range(3):
        repo.add_absence(fx.id, Absence(team="away", player=f"Star {i}", kind="injury", importance="key"))
    after = analysis.analyze(fx, settings, NOW + timedelta(minutes=10))
    p1 = _sel(after, "Alpha FC")["final_prob"]
    assert p1 > p0  # away injuries favour the home team
    assert p1 - p0 < 0.10  # but the adjustment stays bounded
    inj = next(ag for ag in after["agents"] if ag["agent"] == "injuries")
    assert inj["lean_home"] > 0 and inj["status"] == "ok"


def _fake_analysis(fid, kickoff, ev, prob, odds, conf=70, risk="low", qualifies=True):
    sel = {"market_id": "h2h", "market": "h2h", "line": None, "market_label": "Match result 1X2", "selection": "home",
           "label": f"Home {fid}", "best": {"bookmaker": "nordicbet", "name": "NordicBet", "odds": odds, "flags": [], "source": "the_odds_api"},
           "final_prob": prob, "fair_odds": 1 / prob, "ev_pct": ev, "risk_rating": risk, "confidence": conf,
           "explanation": "x", "pricing_flags": [], "rejections": [] if qualifies else ["EV too low"], "qualifies": qualifies}
    return {"fixture": {"id": fid, "home": f"H{fid}", "away": f"A{fid}", "sport": "football", "kickoff": kickoff.isoformat(),
                        "status": "scheduled", "is_demo": False},
            "competition": {"name": "Test"}, "selections": [sel]}


def test_daily_picks_rules(settings):
    ko = NOW + timedelta(hours=3)
    analyses = [_fake_analysis(i, ko, ev=6 + i, prob=0.55, odds=1.95) for i in range(7)]
    d = build_daily_picks(analyses, {**settings, "coupon_odds_mode": "fixed"}, NOW)
    assert 1 <= len(d["coupons"]) <= 3 and d["message"] is None
    seen = set()
    for c in d["coupons"]:
        assert 1 <= len(c["legs"]) <= 3
        assert c["combined_odds"] > 3.0
        ids = {leg["fixture_id"] for leg in c["legs"]}
        assert len(ids) == len(c["legs"]) and not (ids & seen)  # different matches across coupons
        seen |= ids
        for leg in c["legs"]:
            for key in ("match", "sport", "competition", "market_label", "selection_label", "bookmaker_name", "odds",
                        "probability", "fair_odds", "ev_pct", "risk_rating", "explanation"):
                assert leg[key] is not None


def test_daily_picks_never_forced(settings):
    ko = NOW + timedelta(hours=3)
    assert build_daily_picks([], settings, NOW)["message"] == NO_PICKS_MESSAGE
    bad = [_fake_analysis(i, ko, ev=-2, prob=0.5, odds=1.9, qualifies=False) for i in range(5)]
    assert build_daily_picks(bad, settings, NOW)["coupons"] == []
    # exactly one good opportunity with odds above 3 -> exactly one coupon
    one = [_fake_analysis(1, ko, ev=8, prob=0.36, odds=3.0 * 1.0 + 0.2)]
    d = build_daily_picks(one, {**settings, "coupon_odds_mode": "fixed"}, NOW)
    assert len(d["coupons"]) == 1 and len(d["coupons"][0]["legs"]) == 1
    # high odds but low probability -> rejected even with positive EV
    longshot = [_fake_analysis(2, ko, ev=10, prob=0.11, odds=10.0)]
    d = build_daily_picks(longshot, settings, NOW)
    assert d["coupons"] == [] and d["diagnostics"]["rejections"]


def test_matches_outside_window_are_ignored(settings):
    far = NOW + timedelta(days=3)
    d = build_daily_picks([_fake_analysis(i, far, ev=9, prob=0.5, odds=2.2) for i in range(4)], settings, NOW)
    assert d["coupons"] == [] and d["diagnostics"]["matches_in_window"] == 0


def test_auto_mode_builds_safe_value_and_bigger_odds_coupons(settings):
    ko = NOW + timedelta(hours=3)
    analyses = [_fake_analysis(i, ko, ev=4 + i, prob=0.58, odds=1.80 + i * 0.01) for i in range(8)]
    d = build_daily_picks(analyses, settings, NOW)  # auto is the default
    assert d["criteria"]["coupon_odds_mode"] == "auto"
    profiles = [c["profile"] for c in d["coupons"]]
    assert profiles[0] == "safe" and "big" in profiles
    safe = d["coupons"][0]
    assert safe["combined_odds"] < 3.0 and safe["combined_ev_pct"] > 0  # not hidden for being under 3.00
    big = next(c for c in d["coupons"] if c["profile"] == "big")
    assert big["combined_odds"] >= 3.0
    seen = set()
    for c in d["coupons"]:
        ids = {leg["fixture_id"] for leg in c["legs"]}
        assert not (ids & seen)
        seen |= ids


def test_auto_mode_uses_strong_candidates_but_never_negative_value(settings):
    ko = NOW + timedelta(hours=3)
    strong = _fake_analysis(1, ko, ev=1.5, prob=0.72, odds=1.41, conf=80, risk="low", qualifies=False)
    strong["selections"][0]["rejection_codes"] = ["ev"]   # only the EV threshold failed
    weak = _fake_analysis(2, ko, ev=1.5, prob=0.50, odds=2.03, conf=80, risk="low", qualifies=False)
    weak["selections"][0]["rejection_codes"] = ["ev"]     # not likely enough to be "strong"
    negative = _fake_analysis(3, ko, ev=-1, prob=0.75, odds=1.30, qualifies=False)
    d = build_daily_picks([strong, weak, negative], settings, NOW)
    legs = [leg for c in d["coupons"] for leg in c["legs"]]
    assert [leg["fixture_id"] for leg in legs] == [1] and legs[0]["tier"] == "strong"
    assert d["coupons"][0]["combined_odds"] < 2.0
    fixed = build_daily_picks([strong, weak, negative], {**settings, "coupon_odds_mode": "fixed"}, NOW)
    assert fixed["coupons"] == []  # fixed mode keeps the strict rules
