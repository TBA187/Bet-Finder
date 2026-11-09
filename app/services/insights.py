"""Cross-match views: dashboard, agent overview, arbitrage finder, dropping
odds, data/credit usage and a The Odds API credit planner."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app.services.analysis import AGENT_ORDER


# ---------------------------------------------------------------- arbitrage
def arbitrage(analyses: list[dict], stake: float = 100.0) -> list[dict]:
    """Markets where backing every outcome at the best prices guarantees a profit."""
    out = []
    for a in analyses:
        for m in a["markets"]:
            if not m.get("arbitrage_pct"):
                continue
            sels = m["selections"]
            inv = {s: 1 / m["best"][s]["odds"] for s in sels}
            total_inv = sum(inv.values())
            legs = [{"selection": s, "label": m["selection_labels"][s], "bookmaker": m["best"][s]["name"],
                     "odds": m["best"][s]["odds"], "stake": round(stake * inv[s] / total_inv, 2),
                     "flags": next((q["flags"] for q in m["quotes"][s] if q["bookmaker"] == m["best"][s]["bookmaker"]), [])}
                    for s in sels]
            out.append({"fixture": a["fixture"], "competition": a["competition"]["name"], "market_label": m["label"],
                        "market_id": m["market_id"], "profit_pct": m["arbitrage_pct"],
                        "profit_amount": round(stake / total_inv - stake, 2), "total_stake": stake, "legs": legs})
    return sorted(out, key=lambda x: -x["profit_pct"])


# ------------------------------------------------------------ dropping odds
def dropping_odds(analyses: list[dict], min_move: float = 5.0) -> list[dict]:
    """Big price moves, sharp bookmakers first ("steam")."""
    out = []
    for a in analyses:
        sharp = set()
        for m in a["markets"]:
            sharp |= set(m["consensus"]["sharp_books"])
        for m in a["markets"]:
            for s in m["selections"]:
                for q in m["quotes"].get(s, []):
                    if abs(q["move_pct"]) >= min_move:
                        out.append({"fixture": a["fixture"], "market_label": m["label"], "label": m["selection_labels"][s],
                                    "bookmaker": q["name"], "from": q["opening_odds"], "to": q["odds"],
                                    "move_pct": q["move_pct"], "sharp": q["bookmaker"] in sharp})
    return sorted(out, key=lambda x: (not x["sharp"], x["move_pct"]))[:30]


# ---------------------------------------------------------------- dashboard
def dashboard(analyses: list[dict], picks: dict, tracker_summary: dict, status: dict) -> dict:
    sels = [(a, s) for a in analyses for s in a["selections"]]
    value = sorted([(a, s) for a, s in sels if s["qualifies"]], key=lambda x: -x[1]["ev_pct"])
    errors = sorted([(a, s) for a, s in sels if s.get("deviation_level") == "alert" and s.get("pricing_flags")],
                    key=lambda x: -(x[1]["market_ev_pct"] or 0))

    def brief(a, s):
        return {"fixture": a["fixture"], "competition": a["competition"]["name"], "label": s["label"],
                "market_label": s["market_label"], "market_id": s["market_id"], "selection": s["selection"],
                "odds": s["best"]["odds"], "bookmaker": s["best"]["name"], "source": s["best"]["source"],
                "ev_pct": s["ev_pct"], "market_ev_pct": s["market_ev_pct"], "final_prob": s["final_prob"],
                "fair_odds": s["fair_odds"], "risk_rating": s.get("risk_rating"), "deviation_level": s.get("deviation_level"),
                "value_status": s.get("value_status"), "confidence": s["confidence"]}

    by_sport: dict[str, int] = {}
    for a in analyses:
        by_sport[a["fixture"]["sport"]] = by_sport.get(a["fixture"]["sport"], 0) + 1
    latest = max((a["odds_updated_at"] for a in analyses if a.get("odds_updated_at")), default=None)
    return {
        "counts": {"matches": len(analyses), "with_odds": sum(1 for a in analyses if a["markets"]),
                   "value_bets": len(value), "pricing_errors": len(errors),
                   "arbitrage": sum(1 for a in analyses for m in a["markets"] if m.get("arbitrage_pct")),
                   "coupons": len(picks.get("coupons", [])), "by_sport": by_sport},
        "odds_updated_at": latest,
        "top_value": [brief(a, s) for a, s in value[:6]],
        "pricing_errors": [brief(a, s) for a, s in errors[:6]],
        "dropping": dropping_odds(analyses)[:6],
        "picks": {"message": picks.get("message"), "coupons": [
            {"id": c["id"], "title": c["title"], "combined_odds": c["combined_odds"], "combined_ev_pct": c["combined_ev_pct"],
             "risk_rating": c["risk_rating"], "legs": len(c["legs"])} for c in picks.get("coupons", [])]},
        "tracker": tracker_summary,
        "status": status,
    }


# ------------------------------------------------------------ agent overview
def agents_overview(analyses: list[dict]) -> list[dict]:
    out = {k: {"agent": k, "name": None, "with_data": 0, "total": 0, "conf_sum": 0.0, "highlights": []} for k in AGENT_ORDER}
    for a in analyses:
        if not a["markets"]:
            continue
        match = f"{a['fixture']['home']} vs {a['fixture']['away']}"
        for ag in a["agents"]:
            o = out[ag["agent"]]
            o["name"] = ag["name"]
            o["total"] += 1
            if ag["status"] in ("ok", "partial"):
                o["with_data"] += 1
                o["conf_sum"] += ag["confidence"]
                strength = max(abs(ag["lean_home"]), abs(ag["lean_total"])) * ag["confidence"]
                if ag["warnings"] or strength > 0.15:
                    o["highlights"].append({"fixture_id": a["fixture"]["id"], "match": match, "summary": ag["summary"],
                                            "warning": ag["warnings"][0] if ag["warnings"] else None,
                                            "strength": round(strength, 3)})
    result = []
    for k in AGENT_ORDER:
        o = out[k]
        o["avg_confidence"] = round(o["conf_sum"] / o["with_data"], 3) if o["with_data"] else None
        o["highlights"] = sorted(o["highlights"], key=lambda h: (h["warning"] is None, -h["strength"]))[:5]
        del o["conf_sum"]
        result.append(o)
    return result


# ------------------------------------------------------------------ usage
def usage(provider_status: dict, settings: dict, providers: list, secret_source, now: datetime) -> dict:
    today, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    last = provider_status.get("_last_refresh") or {}
    rows = []
    for p in providers:
        st = {}
        for k, v in provider_status.items():
            if k == p.name or k.startswith(p.name + "_"):
                for field in ("requests_by_day", "requests_by_month"):
                    for kk, vv in v.get(field, {}).items():
                        st.setdefault(field, {}).setdefault(kk, 0)
                        st[field][kk] += vv
                for field in ("last_ok", "last_error", "last_error_at", "remaining", "used", "remaining_by_sport",
                              "exhausted", "blocked", "block_reasons", "last_request_at"):
                    if field in v and field not in st:
                        st[field] = v[field]
        tokens = None
        if p.name == "the_odds_api" and st.get("remaining") is not None:
            used, remaining = float(st.get("used") or 0), float(st["remaining"])
            tokens = {"unit": "credits", "period": "month", "used": used, "remaining": remaining,
                      "limit": used + remaining, "exact": True}
        elif p.name == "api_sports" and st.get("remaining_by_sport"):
            tokens = {"unit": "requests", "period": "day", "remaining_by_sport": st["remaining_by_sport"], "exact": True}
        elif p.name == "oddspapi":
            used = st.get("requests_by_month", {}).get(month, 0)
            budget = int(settings.get("oddspapi_monthly_budget", 240))
            tokens = {"unit": "requests", "period": "month", "used": used, "remaining": max(0, 250 - used),
                      "limit": 250, "budget": budget, "exact": False}
        elif p.name == "odds_api_io":
            tokens = {"unit": "requests", "period": "hour", "limit": 5000, "exact": False,
                      "used": st.get("requests_by_day", {}).get(today, 0)}
        key_name = {"the_odds_api": "odds_api_key", "api_sports": "api_sports_key", "odds_api_io": "odds_api_io_key",
                    "oddspapi": "oddspapi_key"}.get(p.name)
        rows.append({
            "provider": p.name, "label": p.label, "method": getattr(p, "method", "api"), "configured": p.configured(),
            "key_source": secret_source(key_name) if key_name else None,
            "requests_today": st.get("requests_by_day", {}).get(today, 0),
            "requests_month": st.get("requests_by_month", {}).get(month, 0),
            "last_request_at": st.get("last_request_at"), "last_ok": st.get("last_ok"),
            "last_error": st.get("last_error"), "last_error_at": st.get("last_error_at"),
            "exhausted": bool(st.get("exhausted")), "tokens": tokens,
            "blocked_hosts": {h: {"until": u, "reason": (st.get("block_reasons") or {}).get(h)}
                              for h, u in (st.get("blocked") or {}).items() if u > now.isoformat()},
        })
    return {"providers": rows, "last_refresh": {"at": last.get("at"), "odds_methods": last.get("odds_methods", []),
                                                 "chain": last.get("chain", [])},
            "scraper_mode": settings.get("scraper_mode", "fallback")}


# ----------------------------------------------------------- credit planner
PLANS = [(500, "Free (Starter)", 0), (20_000, "20K", 30), (100_000, "100K", 59), (5_000_000, "5M", 119)]


def credit_plan(settings: dict, competitions: list[dict]) -> dict:
    odds_comps = [c for c in competitions if c.get("enabled") and c.get("providers", {}).get("the_odds_api")]
    feeds = sum(2 if "*" in c["providers"]["the_odds_api"] else 1 for c in odds_comps)
    per_feed = len(settings["odds_api_regions"]) * len(settings["odds_api_markets"])
    active_share = 0.6  # the free events check skips leagues without matches in the window
    per_refresh = round(feeds * per_feed * active_share)
    minutes = int(settings["auto_refresh_minutes"])
    start, end = int(settings["active_hours_start"]), int(settings["active_hours_end"])
    hours = (end - start) if end > start else (24 - start + end)
    per_day = (hours * 60 / minutes) if minutes > 0 else 3
    monthly = round(per_refresh * per_day * 30)
    plan = next(((name, price) for limit, name, price in PLANS if monthly <= limit), (PLANS[-1][1], PLANS[-1][2]))
    return {"feeds": feeds, "credits_per_feed": per_feed, "credits_per_refresh": per_refresh,
            "refreshes_per_day": round(per_day, 1), "monthly_credits": monthly,
            "recommended_plan": plan[0], "price_usd": plan[1],
            "plans": [{"name": n, "credits": lim, "price_usd": pr} for lim, n, pr in PLANS],
            "assumption": "manual refresh about 3 times a day" if minutes <= 0 else f"auto refresh every {minutes} min, {hours} h/day"}
