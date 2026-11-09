"""Daily Picks: up to three coupons (configurable 1-5) of up to three legs,
combined odds above the target (3.00 by default), built ONLY from qualifying
value selections.

Rules:
* one selection per match, and coupons never share a match,
* never forced: one good coupon gives one coupon, none gives the message
  "No qualifying value bets found today.",
* high odds alone never qualify.  Every leg needs positive EV above the
  threshold, a price at or above the fair market price, enough confidence,
  acceptable risk and a minimum probability,
* coupons are ranked by expected log-growth (Kelly), which rewards edge but
  penalises long-shot combinations.
"""
from __future__ import annotations

import itertools
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from app.config import RISK_LEVELS, tz
from app.core.odds_math import combined_odds, combined_probability, kelly_fraction, log_growth
from app.i18n import lang_of, tr

NO_PICKS_MESSAGE = "No qualifying value bets found today."
MAX_CANDIDATES = 30


def day_window(now: datetime, settings: dict) -> tuple[datetime, datetime, str]:
    """From now until tomorrow's end-of-day hour (default 06:00 local).

    At 19:00 the window runs to 06:00 tomorrow (tonight's matches).  After
    midnight (e.g. 02:00) it also runs to 06:00 *tomorrow*, so the coming day
    is included instead of a 4-hour window with almost no matches."""
    zone = tz(settings)
    local = now.astimezone(zone)
    end_hour = int(settings.get("day_end_hour", 6))
    end = local.replace(hour=end_hour, minute=0, second=0, microsecond=0) + timedelta(days=1)
    day = local.date().isoformat()
    return now, end.astimezone(now.tzinfo), day


def _leg(analysis: dict, sel: dict) -> dict:
    fx = analysis["fixture"]
    return {
        "fixture_id": fx["id"],
        "match": f"{fx['home']} vs {fx['away']}",
        "sport": fx["sport"],
        "competition": analysis["competition"]["name"],
        "kickoff": fx["kickoff"],
        "is_demo": fx["is_demo"],
        "market_id": sel["market_id"],
        "market": sel["market"],
        "line": sel["line"],
        "market_label": sel["market_label"],
        "selection": sel["selection"],
        "selection_label": sel["label"],
        "bookmaker": sel["best"]["bookmaker"],
        "bookmaker_name": sel["best"]["name"],
        "source": sel["best"]["source"],
        "odds": sel["best"]["odds"],
        "probability": sel["final_prob"],
        "fair_odds": sel["fair_odds"],
        "ev_pct": sel["ev_pct"],
        "risk_rating": sel["risk_rating"],
        "confidence": sel["confidence"],
        "explanation": sel["explanation"],
        "pricing_flags": sel.get("pricing_flags", []),
        "deviation_level": sel.get("deviation_level"),
        "stake_amount": sel.get("stake_amount"),
    }


def build_daily_picks(analyses: list[dict], settings: dict, now: datetime) -> dict:
    lang = lang_of(settings)
    start, end, day = day_window(now, settings)
    min_prob = float(settings["min_leg_probability"])
    max_odds = float(settings["max_leg_odds"])
    target = float(settings["coupon_min_odds"])
    max_legs = int(settings["coupon_max_legs"])
    max_coupons = int(settings["max_coupons"])

    rejections: Counter = Counter()
    candidates: list[dict] = []
    in_window = 0
    for a in analyses:
        fx = a["fixture"]
        kickoff = datetime.fromisoformat(fx["kickoff"])
        if fx["status"] != "scheduled" or not (start <= kickoff <= end):
            continue
        in_window += 1
        best_for_match: Optional[dict] = None
        for s in a["selections"]:
            if s["ev_pct"] is None or s["ev_pct"] <= 0:
                continue
            codes = list(s.get("rejection_codes", []))
            if s["final_prob"] is not None and s["final_prob"] < min_prob:
                codes.append("probability")
            if s["best"]["odds"] > max_odds:
                codes.append("odds")
            if codes:
                for c in codes:
                    rejections[c] += 1
                continue
            score = s["ev_pct"] * s["confidence"]
            if best_for_match is None or score > best_for_match["_score"]:
                best_for_match = {**_leg(a, s), "_score": score}
        if best_for_match:
            candidates.append(best_for_match)
    candidates.sort(key=lambda c: -c["_score"])
    candidates = candidates[:MAX_CANDIDATES]

    coupons = []
    used: set[str] = set()
    for idx in range(max_coupons):
        pool = [c for c in candidates if c["fixture_id"] not in used]
        best = _best_combo(pool, max_legs, target)
        if not best:
            break
        coupons.append(_coupon(best, idx + 1, settings, lang))
        used |= {leg["fixture_id"] for leg in best}

    singles = [_strip(c) for c in candidates if c["fixture_id"] not in used]
    return {
        "date": day,
        "generated_at": now.isoformat(),
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "coupons": coupons,
        "message": None if coupons else tr(lang, "dp_none"),
        "singles": singles,
        "diagnostics": {
            "matches_in_window": in_window,
            "qualifying_selections": len(candidates),
            "rejections": dict(rejections.most_common()),
            "note": tr(lang, "dp_note_no_combo", n=max_legs, target=f"{target:.2f}") if candidates and not coupons else None,
        },
        "criteria": {
            "min_ev_pct": settings["min_ev_pct"], "min_confidence": settings["min_confidence"],
            "max_risk": settings["max_risk"], "min_market_edge_pct": settings["min_market_edge_pct"],
            "min_leg_probability": min_prob, "max_leg_odds": max_odds,
            "coupon_min_odds": target, "coupon_max_legs": max_legs, "max_coupons": max_coupons,
            "my_bookmakers": settings.get("my_bookmakers") or [],
        },
    }


def _best_combo(pool: list[dict], max_legs: int, target: float) -> Optional[list[dict]]:
    best, best_score = None, 0.0
    for size in range(1, max_legs + 1):
        for combo in itertools.combinations(pool, size):
            o = combined_odds(c["odds"] for c in combo)
            if o <= target:
                continue
            p = combined_probability(c["probability"] for c in combo)
            f = kelly_fraction(p, o)
            if f <= 0:
                continue
            conf = sum(c["confidence"] for c in combo) / size / 100
            score = log_growth(p, o, f) * conf
            if score > best_score:
                best, best_score = list(combo), score
    return best


def _strip(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_")}


def _coupon(legs: list[dict], number: int, settings: dict, lang: str) -> dict:
    o = combined_odds(leg["odds"] for leg in legs)
    p = combined_probability(leg["probability"] for leg in legs)
    risk_idx = max(RISK_LEVELS.index(leg["risk_rating"]) for leg in legs)
    if p < 0.25:
        risk_idx = max(risk_idx, RISK_LEVELS.index("high"))
    stake = kelly_fraction(p, o) * float(settings["kelly_fraction"])
    return {
        "id": "",
        "title": tr(lang, "dp_coupon", n=number),
        "legs": [_strip(leg) for leg in sorted(legs, key=lambda x: x["kickoff"])],
        "combined_odds": round(o, 2),
        "combined_probability": round(p, 4),
        "combined_fair_odds": round(1 / p, 2),
        "combined_ev_pct": round((p * o - 1) * 100, 2),
        "risk_rating": RISK_LEVELS[risk_idx],
        "stake_pct": round(stake * 100, 2),
        "stake_amount": round(stake * float(settings["bankroll"]), 2),
        "currency": settings.get("currency", "DKK"),
        "is_demo": any(leg["is_demo"] for leg in legs),
        "note": tr(lang, "dp_indep"),
    }
