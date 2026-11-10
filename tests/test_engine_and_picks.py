from datetime import timedelta

import pytest

from app.agents.assessment import FormAgent
from app.core.models import Absence
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
                        "status": "scheduled"},
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


# ------------------------------------------------------------------ v1.6: a fair price that can be trusted
def _basket(repo, home, away="Guests"):
    return make_fixture(repo, sport="basketball", home=home, away=away, competition_id="basketball.nba", competition_name="NBA")


def _market(a, mid="h2h"):
    return next(m for m in a["markets"] if m["market_id"] == mid)


def _role(market, book):
    return next(b for b in market["books"] if b["bookmaker"] == book)["role"]


def test_dead_exchange_market_is_not_a_reference_price(repo, analysis, settings):
    fx = _basket(repo, "Paris", "Fener")
    add_prices(repo, fx, "betfair_ex", {"home": 1.31, "away": 1.33})  # 51% overround: nobody is trading here
    for book, home, away in (("sport888", 3.50, 1.30), ("betsson", 3.50, 1.30), ("coolbet", 3.42, 1.32),
                             ("unibet", 3.30, 1.33), ("nordicbet", 3.45, 1.30)):
        add_prices(repo, fx, book, {"home": home, "away": away})
    a = analysis.analyze(fx, settings, NOW)
    m = _market(a)
    assert m["consensus"]["sharp_books"] == [] and m["consensus"]["anchor"] is None
    assert _role(m, "betfair_ex") == "ignored"
    assert all(v["bookmaker"] != "betfair_ex" for views in m["quotes"].values() for v in views)  # never offered as a price
    home = _sel(a, "Paris")
    assert home["best"]["odds"] == 3.50 and home["ev_pct"] < 0 and not home["qualifies"]  # was "+56% value" before


def test_thin_or_contradicting_exchange_is_demoted(repo, analysis, settings):
    thin = _basket(repo, "Sixers", "Celtics")
    add_prices(repo, thin, "betfair_ex", {"home": 1.85, "away": 1.92})  # 6.1% overround: an ordinary price at best
    for book, home, away in (("sport888", 2.00, 1.80), ("betsson", 1.88, 1.92), ("unibet", 1.85, 1.95)):
        add_prices(repo, thin, book, {"home": home, "away": away})
    m = _market(analysis.analyze(thin, settings, NOW))
    assert _role(m, "betfair_ex") == "soft" and m["consensus"]["anchor"] is None and m["consensus"]["method"] == "median"

    off = _basket(repo, "Lakers", "Hawks")
    add_prices(repo, off, "betfair_ex", {"home": 1.30, "away": 4.30})  # tight, but far from what every bookmaker says
    for book in ("sport888", "betsson", "unibet"):
        add_prices(repo, off, book, {"home": 1.90, "away": 1.90})
    m = _market(analysis.analyze(off, settings, NOW))
    assert _role(m, "betfair_ex") == "ignored" and m["consensus"]["sharp_books"] == []

    liquid = _basket(repo, "Suns", "Spurs")
    add_prices(repo, liquid, "betfair_ex", {"home": 4.20, "away": 1.30})  # 0.7% overround: as good as a sharp bookmaker
    add_prices(repo, liquid, "unibet", {"home": 3.40, "away": 1.33})
    m = _market(analysis.analyze(liquid, settings, NOW))
    assert _role(m, "betfair_ex") == "anchor" and m["consensus"]["anchor"] == "strong"


def test_hockey_regulation_time_totals_are_not_mixed_with_overtime_totals(repo, analysis, settings):
    fx = make_fixture(repo, sport="icehockey", home="Oilers", away="Rangers", competition_id="icehockey.nhl", competition_name="NHL")
    for book in ("pinnacle", "sport888", "williamhill"):  # moneyline incl. overtime -> totals incl. overtime
        add_prices(repo, fx, book, {"home": 1.63, "away": 2.35})
        add_prices(repo, fx, book, {"over": 1.91, "under": 1.91}, market="totals", line=6.5)
    for book in ("unibet", "coolbet", "onexbet"):  # 3-way regulation result -> totals for regulation time
        add_prices(repo, fx, book, {"home": 2.08, "draw": 4.40, "away": 3.05}, market="h2h3")
        add_prices(repo, fx, book, {"over": 2.15, "under": 1.71}, market="totals", line=6.5)
        add_prices(repo, fx, book, {"over": 1.67, "under": 2.20}, market="totals", line=5.5)
    a = analysis.analyze(fx, settings, NOW)
    mixed, alone = _market(a, "totals@6.5"), _market(a, "totals@5.5")
    assert {b["bookmaker"] for b in mixed["books"]} == {"pinnacle", "sport888", "williamhill"}
    assert {e["bookmaker"] for e in mixed["excluded_books"]} == {"unibet", "coolbet", "onexbet"}
    assert all(e["reason"] == "regulation_time" for e in mixed["excluded_books"])
    # a line that only regulation-time bookmakers price is consistent and stays
    assert {b["bookmaker"] for b in alone["books"]} == {"unibet", "coolbet", "onexbet"} and alone["excluded_books"] == []
    over = next(s for s in a["selections"] if s["market_id"] == "totals@6.5" and s["selection"] == "over")
    assert over["best"]["odds"] == 1.91 and over["ev_pct"] < 0  # 2.15 is a different bet, not "value"


def test_exchange_prices_count_after_commission(repo, analysis, settings):
    fx = _basket(repo, "Rockets", "Nuggets")
    add_prices(repo, fx, "pinnacle", {"home": 1.31, "away": 3.70})
    add_prices(repo, fx, "betfair_ex", {"home": 1.35, "away": 3.85})  # best price, before 5% commission on winnings
    for book in ("sport888", "betsson", "unibet"):
        add_prices(repo, fx, book, {"home": 1.29, "away": 3.60})
    home = _sel(analysis.analyze(fx, settings, NOW), "Rockets")
    assert home["best"]["bookmaker"] == "betfair_ex" and home["best"]["odds"] == 1.35
    assert home["best"]["commission_pct"] == 5.0 and home["best"]["net_odds"] == pytest.approx(1.3325)
    assert home["ev_pct"] == pytest.approx((home["final_prob"] * 1.3325 - 1) * 100, abs=0.02)
    assert "1.33" in home["explanation"]  # the explanation says what the price is worth after commission


def test_value_must_hold_against_the_other_bookmakers(repo, analysis, settings):
    softs = ("sport888", "betsson", "unibet", "nordicbet", "coolbet")
    # The value rests on the sharp bookmaker alone: the price is far below what every other
    # bookmaker considers fair.  Then the reference is usually the one that is off: not trusted.
    fx = _basket(repo, "Alba", "Bayern")
    add_prices(repo, fx, "pinnacle", {"home": 2.40, "away": 1.62})
    for book in softs:
        add_prices(repo, fx, book, {"home": 2.00, "away": 1.75})
    away = _sel(analysis.analyze(fx, settings, NOW), "Bayern")
    assert away["ev_pct"] > 1.5 and away["soft_ev_pct"] < -4
    assert "uncorroborated" in away["rejection_codes"] and not away["qualifies"]

    # The ordinary bookmakers merely lag the sharp one by a little: the classic value bet, accepted.
    lag = _basket(repo, "Ulm", "Bonn")
    add_prices(repo, lag, "pinnacle", {"home": 2.30, "away": 1.68})
    for book in softs:
        add_prices(repo, lag, book, {"home": 2.05, "away": 1.80})
    classic = _sel(analysis.analyze(lag, settings, NOW), "Bonn")
    assert classic["ev_pct"] > 1.5 and -4 < classic["soft_ev_pct"] < -1 and classic["qualifies"]
    off = analysis.analyze(fx, {**settings, "min_soft_edge_pct": -100.0}, NOW + timedelta(minutes=6))
    assert "uncorroborated" not in _sel(off, "Bayern")["rejection_codes"]  # the check can be switched off

    # One bookmaker is out of line with the sharp bookmaker AND with the others: that is value.
    fx2 = _basket(repo, "Milano", "Zalgiris")
    add_prices(repo, fx2, "pinnacle", {"home": 1.95, "away": 1.95})
    for book in ("sport888", "betsson", "unibet", "coolbet"):
        add_prices(repo, fx2, book, {"home": 1.90, "away": 1.90})
    add_prices(repo, fx2, "nordicbet", {"home": 2.06, "away": 1.78})
    home = _sel(analysis.analyze(fx2, settings, NOW), "Milano")
    assert home["best"]["bookmaker"] == "nordicbet" and home["soft_ev_pct"] > 1.5
    assert home["qualifies"] and home["rejection_codes"] == []
    assert home["min_odds"] == pytest.approx(2.03)  # the lowest price that still has the required +1.5%


def test_small_edges_need_a_sharp_reference(repo, analysis, settings):
    def build(home, with_pinnacle):
        fx = _basket(repo, home)
        if with_pinnacle:
            add_prices(repo, fx, "pinnacle", {"home": 1.95, "away": 1.95})
        for book in ("sport888", "betsson", "unibet", "coolbet"):
            add_prices(repo, fx, book, {"home": 1.90, "away": 1.90})
        add_prices(repo, fx, "nordicbet", {"home": 2.04, "away": 1.80})
        return _sel(analysis.analyze(fx, settings, NOW), home)

    anchored = build("Anchored", True)
    assert anchored["anchor"] == "strong" and anchored["required_ev_pct"] == 1.5
    assert 1.5 <= anchored["ev_pct"] < 3 and "ev" not in anchored["rejection_codes"]
    loose = build("Loose", False)
    assert loose["anchor"] is None and loose["required_ev_pct"] == 3.0
    assert 1.5 <= loose["ev_pct"] < 3 and "ev" in loose["rejection_codes"]


# ------------------------------------------------------------------ v1.6: Daily Picks
def test_daily_picks_say_when_the_odds_are_too_old(settings):
    a = _fake_analysis(1, NOW + timedelta(hours=3), ev=5, prob=0.5, odds=2.1)
    a["selections"][0].update(ev_pct=None, final_prob=None)  # stale prices give no estimate at all
    a["odds_updated_at"] = (NOW - timedelta(hours=6)).isoformat()
    d = build_daily_picks([a], settings, NOW)
    assert d["coupons"] == [] and d["diagnostics"]["stale"] and d["diagnostics"]["odds_age_minutes"] == 360
    assert d["message"] != NO_PICKS_MESSAGE and "6 hour" in d["message"]
    a["odds_updated_at"] = (NOW - timedelta(minutes=20)).isoformat()  # fresh prices, simply nothing to estimate
    d = build_daily_picks([a], settings, NOW)
    assert not d["diagnostics"]["stale"] and d["message"] == NO_PICKS_MESSAGE


def test_coming_days_scope_starts_where_today_ends(settings):
    today = _fake_analysis(1, NOW + timedelta(hours=3), ev=6, prob=0.55, odds=1.95)
    later = _fake_analysis(2, NOW + timedelta(hours=40), ev=6, prob=0.55, odds=1.95)
    far = _fake_analysis(3, NOW + timedelta(hours=200), ev=6, prob=0.55, odds=1.95)  # beyond the lookahead
    d_today = build_daily_picks([today, later, far], settings, NOW)
    d_next = build_daily_picks([today, later, far], settings, NOW, scope="upcoming")
    assert [leg["fixture_id"] for c in d_today["coupons"] for leg in c["legs"]] == [1]
    assert [leg["fixture_id"] for c in d_next["coupons"] for leg in c["legs"]] == [2]
    assert d_today["scope"] == "today" and d_next["scope"] == "upcoming"
    assert d_next["window"]["from"] == d_today["window"]["to"]
    empty = build_daily_picks([today], settings, NOW, scope="upcoming")
    assert empty["coupons"] == [] and "coming days" in empty["message"]


def test_coupon_says_when_its_legs_are_at_different_bookmakers(settings):
    ko = NOW + timedelta(hours=3)
    a, b = _fake_analysis(1, ko, ev=4, prob=0.40, odds=2.60), _fake_analysis(2, ko, ev=4, prob=0.40, odds=2.60)
    b["selections"][0]["best"].update(bookmaker="unibet", name="Unibet")
    for x in (a, b):
        x["selections"][0]["min_odds"] = 2.54
    (coupon,) = build_daily_picks([a, b], {**settings, "coupon_odds_mode": "fixed", "coupon_min_odds": 5.0}, NOW)["coupons"]
    assert len(coupon["legs"]) == 2 and coupon["mixed_books"]  # two bets, not one
    assert all(leg["min_odds"] == 2.54 for leg in coupon["legs"])
    single = build_daily_picks([a], settings, NOW)["coupons"][0]
    assert single["mixed_books"] is False


def test_ranking_does_not_chase_the_largest_claimed_edge(settings):
    ko = NOW + timedelta(hours=3)
    huge = _fake_analysis(1, ko, ev=40, prob=0.50, odds=2.80)   # a claimed +40% is far more often an error than a gift
    solid = _fake_analysis(2, ko, ev=5, prob=0.70, odds=1.50)
    # no coupon can reach odds of 50, so every candidate is listed as a single, best first
    d = build_daily_picks([huge, solid], {**settings, "coupon_odds_mode": "fixed", "coupon_min_odds": 50.0}, NOW)
    assert d["coupons"] == [] and [s["fixture_id"] for s in d["singles"]] == [2, 1]


def test_daily_picks_endpoint_has_a_coming_days_scope(client):
    d = client.get("/api/daily-picks?scope=upcoming").json()
    assert d["scope"] == "upcoming" and "coming days" in d["message"] and d["diagnostics"]["stale"] is False
    assert client.get("/api/daily-picks").json()["scope"] == "today"
    assert client.get("/api/daily-picks?scope=nonsense").status_code == 422
    s = client.get("/api/settings").json()
    assert (s["min_ev_pct"], s["min_ev_weak_anchor_pct"], s["min_soft_edge_pct"]) == (1.5, 3.0, -4.0)
