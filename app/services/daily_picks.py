"""Daily Picks: up to three coupons (configurable 1-5) of up to three legs.

Two ways to decide the coupon odds:

* **auto** (default, "smart"): no fixed minimum.  BetFinder builds different
  kinds of coupons and keeps the strongest of each kind:
    - Safe        - the highest chance of winning (often 1-2 legs, odds can be
                    well under 2.00) while still priced above the fair odds,
    - Value       - the best balance of edge and chance (Kelly growth),
    - Bigger odds - combined odds of 3.00 or more for a bigger return.
  Besides the qualifying value bets, auto mode also uses *strong candidates*:
  selections that are likely (>= 60 %), low risk, confident and priced at or
  above the fair market price, but whose edge is below the EV threshold.
* **fixed**: every coupon must have combined odds above ``coupon_min_odds``
  (3.00 by default), ranked by Kelly growth - the classic behaviour.

Two scopes:
* **today**: from now until tomorrow's end-of-day hour,
* **upcoming**: the days after that, as far as odds are fetched (early prices).

Always true:
* one selection per match, and coupons never share a match,
* never forced: no good coupon gives the message
  "No qualifying value bets found today." - or, when the stored prices are
  simply too old to judge, a message that says so,
* every leg has positive expected value; high odds alone never qualify.

Legs of one coupon can be priced at different bookmakers.  Such a coupon
cannot be placed as one bet: each leg is its own bet (``mixed_books``).
"""
from __future__ import annotations

import itertools
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable, Optional

from app.config import RISK_LEVELS, tz
from app.core.odds_math import combined_odds, combined_probability, kelly_fraction, log_growth
from app.i18n import lang_of, tr

NO_PICKS_MESSAGE = "No qualifying value bets found today."
MAX_CANDIDATES = 30
AUTO_FLOOR = 1.30           # below this a coupon pays too little to be worth it
STRONG_MIN_PROB = 0.60      # "strong candidate": likely ...
STRONG_MAX_RISK = "low"     # ... low risk ...
STRONG_EXTRA_CONF = 5       # ... a bit more confident than the minimum ...
STRONG_MIN_EV = 1.0         # ... and still a real (small) edge
SAFE_MIN_PROB = 0.45
BIG_ODDS = 3.0
# Ranking does not reward an edge beyond this.  A very large edge is most often a pricing error
# that the bookmaker can void, or a problem in the data: it should not outrank a solid, likely pick.
EV_SCORE_CAP = 6.0
PROFILES = ("safe", "value", "big")
SCOPES = ("today", "upcoming")


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


def upcoming_window(now: datetime, settings: dict) -> tuple[datetime, datetime, str]:
    """The days after today's window, as far ahead as odds are fetched (``lookahead_hours``)."""
    _start, today_end, day = day_window(now, settings)
    horizon = now + timedelta(hours=int(settings.get("lookahead_hours", 72)))
    return today_end, max(horizon, today_end), day


def _price(leg: dict) -> float:
    """What the leg really pays: exchange prices count after commission."""
    return leg.get("net_odds") or leg["odds"]


def _leg(analysis: dict, sel: dict, tier: str) -> dict:
    fx = analysis["fixture"]
    best = sel["best"]
    return {
        "fixture_id": fx["id"],
        "match": f"{fx['home']} vs {fx['away']}",
        "sport": fx["sport"],
        "competition": analysis["competition"]["name"],
        "kickoff": fx["kickoff"],
        "market_id": sel["market_id"],
        "market": sel["market"],
        "line": sel["line"],
        "market_label": sel["market_label"],
        "selection": sel["selection"],
        "selection_label": sel["label"],
        "bookmaker": best["bookmaker"],
        "bookmaker_name": best["name"],
        "source": best["source"],
        "odds": best["odds"],
        "net_odds": best.get("net_odds") or best["odds"],
        "commission_pct": best.get("commission_pct"),
        "min_odds": sel.get("min_odds"),  # only bet while the price is still at least this
        "probability": sel["final_prob"],
        "fair_odds": sel["fair_odds"],
        "ev_pct": sel["ev_pct"],
        "required_ev_pct": sel.get("required_ev_pct"),
        "anchor": sel.get("anchor"),
        "risk_rating": sel["risk_rating"],
        "confidence": sel["confidence"],
        "explanation": sel["explanation"],
        "pricing_flags": sel.get("pricing_flags", []),
        "deviation_level": sel.get("deviation_level"),
        "stake_amount": sel.get("stake_amount"),
        "tier": tier,  # "value" = meets every rule, "strong" = likely + low risk, edge below the EV threshold
    }


def _is_strong(sel: dict, codes: list[str], settings: dict) -> bool:
    """Only the EV threshold failed, and everything else says this is a solid pick."""
    return (codes == ["ev"] and (sel["final_prob"] or 0) >= STRONG_MIN_PROB and sel["ev_pct"] >= STRONG_MIN_EV
            and sel.get("risk_rating") in RISK_LEVELS[:RISK_LEVELS.index(STRONG_MAX_RISK) + 1]
            and sel["confidence"] >= int(settings["min_confidence"]) + STRONG_EXTRA_CONF)


def build_daily_picks(analyses: list[dict], settings: dict, now: datetime, scope: str = "today") -> dict:
    lang = lang_of(settings)
    scope = scope if scope in SCOPES else "today"
    start, end, day = upcoming_window(now, settings) if scope == "upcoming" else day_window(now, settings)
    mode = settings.get("coupon_odds_mode", "auto")
    min_prob = float(settings["min_leg_probability"])
    max_odds = float(settings["max_leg_odds"])
    target = float(settings["coupon_min_odds"])
    max_legs = int(settings["coupon_max_legs"])
    max_coupons = int(settings["max_coupons"])

    rejections: Counter = Counter()
    candidates: list[dict] = []
    in_window = priced = 0
    newest: Optional[datetime] = None
    for a in analyses:
        fx = a["fixture"]
        kickoff = datetime.fromisoformat(fx["kickoff"])
        inside = start < kickoff <= end if scope == "upcoming" else start <= kickoff <= end
        if fx["status"] != "scheduled" or not inside:
            continue
        in_window += 1
        if any(s["ev_pct"] is not None for s in a["selections"]):
            priced += 1
        if a.get("odds_updated_at"):
            stamp = datetime.fromisoformat(a["odds_updated_at"])
            newest = stamp if newest is None or stamp > newest else newest
        best_for_match: Optional[dict] = None
        for s in a["selections"]:
            if s["ev_pct"] is None or s["ev_pct"] <= 0:
                continue
            codes = list(s.get("rejection_codes", []))
            if not codes and not s.get("qualifies", True):
                codes = ["other"]
            hard = []
            if s["final_prob"] is not None and s["final_prob"] < min_prob:
                hard.append("probability")
            if s["best"]["odds"] > max_odds:
                hard.append("odds")
            tier = "value" if not codes else "strong" if mode == "auto" and _is_strong(s, codes, settings) else None
            if hard or tier is None:
                for c in codes + hard:
                    rejections[c] += 1
                continue
            # value legs first; within a tier: edge x confidence, plus a bonus for likely outcomes
            score = ((min(s["ev_pct"], EV_SCORE_CAP) + 10 * (s["final_prob"] or 0)) * s["confidence"]
                     * (1.0 if tier == "value" else 0.6))
            if best_for_match is None or score > best_for_match["_score"]:
                best_for_match = {**_leg(a, s, tier), "_score": score}
        if best_for_match:
            candidates.append(best_for_match)
    candidates.sort(key=lambda c: -c["_score"])
    candidates = candidates[:MAX_CANDIDATES]

    coupons = []
    used: set[str] = set()
    for idx in range(max_coupons):
        pool = [c for c in candidates if c["fixture_id"] not in used]
        if mode == "fixed":
            picked = _best_combo(pool, max_legs, _value_score(target))
            profile = "fixed" if picked else None
        else:
            picked, profile = None, None
            order = PROFILES[idx % len(PROFILES):] + PROFILES[:idx % len(PROFILES)]
            for prof in order:  # this slot's kind first, otherwise whatever still works
                picked = _best_combo(pool, max_legs, AUTO_SCORES[prof])
                if picked:
                    profile = prof
                    break
        if not picked:
            break
        coupon = _coupon(picked, idx + 1, profile, settings, lang)
        same = sum(1 for cp in coupons if cp["profile"] == profile)
        if same and profile != "fixed":
            coupon["title"] += f" {same + 1}"
        coupons.append(coupon)
        used |= {leg["fixture_id"] for leg in picked}

    # Nothing can be judged from old prices: say that instead of "no value".
    age_min = (now - newest).total_seconds() / 60 if newest else None
    stale = bool(in_window and not priced and age_min is not None and age_min > float(settings.get("stale_minutes", 240)))
    if coupons:
        message = None
    elif stale:
        message = tr(lang, "dp_stale", h=max(1, round(age_min / 60)))
    else:
        message = tr(lang, "dp_none" if scope == "today" else "dp_none_upcoming")

    singles = [_strip(c) for c in candidates if c["fixture_id"] not in used]
    note = None
    if candidates and not coupons:
        note = tr(lang, "dp_note_no_combo", n=max_legs, target=f"{target:.2f}") if mode == "fixed" else tr(lang, "dp_note_auto")
    return {
        "date": day,
        "scope": scope,
        "generated_at": now.isoformat(),
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "coupons": coupons,
        "message": message,
        "singles": singles,
        "diagnostics": {
            "matches_in_window": in_window,
            "matches_priced": priced,
            "qualifying_selections": len(candidates),
            "value_selections": sum(1 for c in candidates if c["tier"] == "value"),
            "strong_candidates": sum(1 for c in candidates if c["tier"] == "strong"),
            "rejections": dict(rejections.most_common()),
            "note": note,
            "stale": stale,
            "odds_updated_at": newest.isoformat() if newest else None,
            "odds_age_minutes": round(age_min) if age_min is not None else None,
        },
        "criteria": {
            "min_ev_pct": settings["min_ev_pct"],
            "min_ev_weak_anchor_pct": settings.get("min_ev_weak_anchor_pct", settings["min_ev_pct"]),
            "min_soft_edge_pct": settings.get("min_soft_edge_pct"),
            "min_confidence": settings["min_confidence"],
            "max_risk": settings["max_risk"], "min_market_edge_pct": settings["min_market_edge_pct"],
            "min_leg_probability": min_prob, "max_leg_odds": max_odds, "coupon_odds_mode": mode,
            "coupon_min_odds": target, "coupon_max_legs": max_legs, "max_coupons": max_coupons,
            "my_bookmakers": settings.get("my_bookmakers") or [],
        },
    }


# ------------------------------------------------------------- coupon scoring
# Each scorer gets (legs, odds, probability, kelly fraction) and returns a score,
# or None when the combination does not fit that kind of coupon.
Scorer = Callable[[tuple, float, float, float], Optional[float]]


def _conf(combo) -> float:
    return sum(c["confidence"] for c in combo) / len(combo) / 100


def _value_score(min_odds: float) -> Scorer:
    def score(combo, o, p, f):
        if o <= min_odds:
            return None
        return log_growth(p, o, f) * _conf(combo)
    return score


def _safe_score(combo, o, p, f):
    """Most likely to win; every leg low/medium risk and still above fair price."""
    if o < AUTO_FLOOR or p < SAFE_MIN_PROB:
        return None
    if any(RISK_LEVELS.index(c["risk_rating"]) > RISK_LEVELS.index("medium") for c in combo):
        return None
    return p * _conf(combo) * (1 + (p * o - 1))  # chance first, edge breaks ties


def _big_score(combo, o, p, f):
    if o < BIG_ODDS:
        return None
    return log_growth(p, o, f) * _conf(combo)


AUTO_SCORES: dict[str, Scorer] = {"safe": _safe_score, "value": _value_score(AUTO_FLOOR), "big": _big_score}


def _best_combo(pool: list[dict], max_legs: int, scorer: Scorer) -> Optional[list[dict]]:
    best, best_score = None, 0.0
    for size in range(1, max_legs + 1):
        for combo in itertools.combinations(pool, size):
            o = combined_odds(_price(c) for c in combo)
            p = combined_probability(c["probability"] for c in combo)
            f = kelly_fraction(p, o)
            if f <= 0:
                continue  # never a coupon without positive expected value
            score = scorer(combo, o, p, f)
            if score is not None and score > best_score:
                best, best_score = list(combo), score
    return best


def _strip(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_")}


def _coupon(legs: list[dict], number: int, profile: str, settings: dict, lang: str) -> dict:
    o = combined_odds(_price(leg) for leg in legs)
    p = combined_probability(leg["probability"] for leg in legs)
    risk_idx = max(RISK_LEVELS.index(leg["risk_rating"]) for leg in legs)
    if p < 0.25:
        risk_idx = max(risk_idx, RISK_LEVELS.index("high"))
    stake = kelly_fraction(p, o) * float(settings["kelly_fraction"])
    return {
        "id": "",
        "title": tr(lang, f"dp_title_{profile}", n=number),
        "profile": profile,
        "legs": [_strip(leg) for leg in sorted(legs, key=lambda x: x["kickoff"])],
        "combined_odds": round(o, 2),
        "combined_probability": round(p, 4),
        "combined_fair_odds": round(1 / p, 2),
        "combined_ev_pct": round((p * o - 1) * 100, 2),
        "risk_rating": RISK_LEVELS[risk_idx],
        "stake_pct": round(stake * 100, 2),
        "stake_amount": round(stake * float(settings["bankroll"]), 2),
        "currency": settings.get("currency", "DKK"),
        "has_strong": any(leg["tier"] == "strong" for leg in legs),
        # legs at different bookmakers cannot be one bet: each leg is placed on its own
        "mixed_books": len({leg["bookmaker"] for leg in legs}) > 1,
        "note": tr(lang, "dp_indep"),
    }
