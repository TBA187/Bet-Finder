import math

import pytest

from app.core import odds_math as om


def test_spec_formulas():
    assert om.implied_probability(2.0) == pytest.approx(0.5)
    assert om.fair_odds(0.25) == pytest.approx(4.0)
    # EV = (p * odds - 1) * 100
    assert om.ev_percent(0.55, 2.0) == pytest.approx(10.0)
    assert om.ev_percent(0.40, 2.0) == pytest.approx(-20.0)


@pytest.mark.parametrize("bad", [0, 1.0, -2, "abc", None, float("nan"), float("inf"), 5000])
def test_invalid_odds_rejected(bad):
    with pytest.raises(om.OddsError):
        om.validate_odds(bad)


@pytest.mark.parametrize("method", om.DEVIG_METHODS)
def test_devig_sums_to_one(method):
    probs = om.devig([1.80, 3.60, 4.75], method)
    assert sum(probs) == pytest.approx(1.0, abs=1e-9)
    assert all(0 < p < 1 for p in probs)
    assert probs[0] > probs[1]  # favourite stays favourite


def test_shin_and_power_take_more_margin_from_longshots():
    odds = [1.30, 5.50, 11.0]
    mult = om.devig(odds, "multiplicative")
    shin = om.devig(odds, "shin")
    power = om.devig(odds, "power")
    assert shin[2] < mult[2] and shin[0] > mult[0]
    assert power[2] < mult[2] and power[0] > mult[0]


def test_devig_no_margin_is_plain_normalisation():
    assert om.devig([2.0, 2.0], "shin") == pytest.approx([0.5, 0.5])


def test_margin_and_arbitrage():
    assert om.margin_percent([1.90, 1.90]) == pytest.approx(5.263, abs=0.01)
    assert om.arbitrage_percent([1.90, 1.90]) is None
    arb = om.arbitrage_percent([2.10, 2.05])
    assert arb is not None and arb > 0


def test_kelly_and_growth():
    assert om.kelly_fraction(0.4, 2.0) == 0.0  # no edge -> no stake
    f = om.kelly_fraction(0.55, 2.0)
    assert f == pytest.approx(0.10)
    assert om.log_growth(0.55, 2.0, f) > 0
    assert om.log_growth(0.55, 2.0, 0.9) < 0  # over-betting loses


def test_combined():
    assert om.combined_odds([1.5, 2.0]) == pytest.approx(3.0)
    assert om.combined_probability([0.5, 0.5]) == pytest.approx(0.25)


def test_logit_roundtrip():
    for p in (0.01, 0.3, 0.5, 0.97):
        assert om.logistic(om.logit(p)) == pytest.approx(p)
    assert math.isfinite(om.logit(0.0)) and math.isfinite(om.logit(1.0))
