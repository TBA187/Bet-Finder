"""Coupon / bet-builder simulator.

Legs from DIFFERENT matches are treated as independent (probabilities multiply).
Legs from the SAME match are usually correlated ("home win" and "over 2.5"
tend to happen together), so for them the joint probability is computed
from the sport model's full score distribution:

    correlation ratio = P_model(all legs win) / product of P_model(each leg)
    P(group)          = ratio x product of the final (market-anchored) leg probabilities

Football uses the exact Poisson/Dixon-Coles score matrix; basketball,
handball, NFL and baseball use a discretised normal margin x total grid.
For other sports same-match correlation is not modelled and a warning is shown.
"""
from __future__ import annotations

import math
from typing import Optional

from app.core.odds_math import combined_odds
from app.sports import model_for
from app.sports.football import FootballModel
from app.sports.normal_model import NormalScoreModel
from app.sports.settle import outcome


def _score_grid(model, theta: dict) -> Optional[list[tuple[int, int, float]]]:
    if isinstance(model, FootballModel) and type(model).__name__ == "FootballModel":
        m = model._m(theta)
        return [(i, j, m[i][j]) for i in range(len(m)) for j in range(len(m)) if m[i][j] > 1e-9]
    if isinstance(model, NormalScoreModel):
        mu, sd_m, tot, sd_t = theta["mu"], theta["sd_m"], theta["total"], theta["sd_t"]
        cells = []
        for m in range(int(mu - 4 * sd_m), int(mu + 4 * sd_m) + 1):
            pm = math.exp(-0.5 * ((m - mu) / sd_m) ** 2)
            for t in range(max(0, int(tot - 4 * sd_t)), int(tot + 4 * sd_t) + 1):
                if (t + m) % 2 or t < abs(m):
                    continue
                cells.append(((t + m) // 2, (t - m) // 2, pm * math.exp(-0.5 * ((t - tot) / sd_t) ** 2)))
        s = sum(c[2] for c in cells)
        return [(h, a, w / s) for h, a, w in cells] if s else None
    return None


def evaluate(legs: list[dict], analyses: dict[str, dict], competitions: dict[str, dict], offered_odds: Optional[float]) -> dict:
    """legs: [{fixture_id, market_id, selection, odds?}]; analyses: fixture_id -> analysis dict."""
    groups: dict[str, list[dict]] = {}
    resolved = []
    warnings = []
    for leg in legs:
        a = analyses.get(leg["fixture_id"])
        if not a:
            warnings.append(f"unknown_fixture:{leg['fixture_id']}")
            continue
        sel = next((s for s in a["selections"] if s["market_id"] == leg["market_id"] and s["selection"] == leg["selection"]), None)
        if not sel:
            warnings.append(f"unknown_selection:{leg['market_id']}:{leg['selection']}")
            continue
        odds = float(leg.get("odds") or sel["best"]["odds"])
        item = {"fixture_id": leg["fixture_id"], "match": f"{a['fixture']['home']} vs {a['fixture']['away']}",
                "sport": a["fixture"]["sport"], "market_id": sel["market_id"], "market": sel["market"], "line": sel["line"],
                "selection": sel["selection"], "label": sel["label"], "market_label": sel["market_label"],
                "odds": odds, "bookmaker": sel["best"]["name"], "probability": sel["final_prob"]}
        resolved.append(item)
        groups.setdefault(leg["fixture_id"], []).append(item)
    group_out = []
    p_total = 1.0
    unknown = False
    for fid, items in groups.items():
        probs = [i["probability"] for i in items]
        if any(p is None for p in probs):
            unknown = True
            group_out.append({"fixture_id": fid, "legs": len(items), "probability": None, "correlation": None})
            continue
        indep = math.prod(probs)
        ratio = 1.0
        modelled = len(items) == 1
        if len(items) > 1:
            a = analyses[fid]
            theta = a["model"].get("final_params")
            comp = competitions.get(a["competition"]["id"], {})
            model = model_for(a["fixture"]["sport"], comp.get("params"))
            grid = _score_grid(model, theta) if theta else None
            if grid:
                joint = sum(w for h, aw, w in grid if all(outcome(i["market"], i["line"], i["selection"], h, aw) == "win" for i in items))
                singles = [sum(w for h, aw, w in grid if outcome(i["market"], i["line"], i["selection"], h, aw) == "win") for i in items]
                base = math.prod(singles)
                if base > 0:
                    ratio = joint / base
                    modelled = True
            if not modelled:
                warnings.append(f"correlation_not_modelled:{fid}")
        p_group = min(ratio * indep, min(probs))
        group_out.append({"fixture_id": fid, "legs": len(items), "probability": round(p_group, 5),
                          "independent_probability": round(indep, 5), "correlation": round(ratio, 3), "modelled": modelled})
        p_total *= p_group
    naive = combined_odds(i["odds"] for i in resolved) if resolved else None
    p = None if unknown or not resolved else p_total
    price = offered_odds or naive
    return {
        "legs": resolved,
        "groups": group_out,
        "probability": round(p, 5) if p is not None else None,
        "fair_odds": round(1 / p, 2) if p else None,
        "naive_combined_odds": round(naive, 2) if naive else None,
        "offered_odds": offered_odds,
        "ev_pct": round((p * price - 1) * 100, 2) if p and price else None,
        "warnings": warnings,
    }
