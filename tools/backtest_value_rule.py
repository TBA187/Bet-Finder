"""Backtest of BetFinder's value rule on public football results.

Question it answers: when a bookmaker's price is above BetFinder's margin-free
consensus, is that edge real - and how large does it have to be?

Data: football-data.co.uk (free CSV files with results, the odds of Pinnacle,
Betfair Exchange and several ordinary bookmakers a day or two before kick-off,
and the closing odds).  The consensus is built with the app's own code
(``app.services.scanner._consensus``), so the numbers describe the real recipe.

The yardstick is closing-line value (CLV): the price taken against Pinnacle's
margin-free closing price.  A rule whose picks beat the closing line is finding
real value; realised profit needs far more bets to say anything.

    python tools/backtest_value_rule.py                 # 4 seasons, 17 leagues
    python tools/backtest_value_rule.py --seasons 2526  # one season
    python tools/backtest_value_rule.py --refresh       # download again

Files are cached in data/runtime/backtest (git-ignored).  Limits: 1X2 only,
UK-facing bookmakers, and no exchange commission (the bet is always placed at
an ordinary bookmaker).
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import DEFAULT_SETTINGS  # noqa: E402
from app.core.odds_math import OddsError, devig, margin_percent, normalise  # noqa: E402
from app.services.daily_picks import build_daily_picks  # noqa: E402
from app.services.scanner import MIN_OTHER_BOOKS, _consensus, book_role  # noqa: E402

SOURCE = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
TOP = ["E0", "SP1", "D1", "I1", "F1"]
OTHER = ["E1", "E2", "D2", "I2", "SP2", "F2", "N1", "B1", "P1", "T1", "G1", "SC0"]
KEYS = ("H", "D", "A")
CACHE = ROOT / "data" / "runtime" / "backtest"
NOT_BOOKMAKERS = {"Max", "Avg", "PS", "BFE"}
MIN_EV, MIN_EV_WEAK = DEFAULT_SETTINGS["min_ev_pct"], DEFAULT_SETTINGS["min_ev_weak_anchor_pct"]
MIN_SOFT = DEFAULT_SETTINGS["min_soft_edge_pct"]


def load(season: str, div: str, refresh: bool) -> list[dict]:
    path = CACHE / f"{season}_{div}.csv"
    if refresh or not path.exists():
        req = urllib.request.Request(SOURCE.format(season=season, div=div), headers={"User-Agent": "BetFinder backtest"})
        try:
            raw = urllib.request.urlopen(req, timeout=40).read()
        except OSError:
            return []
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        time.sleep(0.2)  # be polite to a free service
    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8-sig", errors="replace"))))


def prices(row: dict, prefix: str) -> dict | None:
    out = {}
    for k in KEYS:
        try:
            out[k] = float(row.get(prefix + k) or 0)
        except ValueError:
            return None
        if out[k] <= 1.0:
            return None
    return out


def fair(p: dict) -> dict:
    return dict(zip(KEYS, devig([p[k] for k in KEYS], "shin")))


def selections(seasons: list[str], refresh: bool) -> tuple[list[dict], list[dict]]:
    """One record per 1X2 outcome: the best price of the ordinary bookmakers against the consensus."""
    sels, matches = [], []
    for season in seasons:
        for div in TOP + OTHER:
            rows = load(season, div, refresh)
            if not rows:
                continue
            cols = set(rows[0])
            names = {c[:-1] for c in cols if c and c.endswith("H") and c[:-1] + "D" in cols and c[:-1] + "A" in cols}
            names -= {n for n in names if n.endswith("C") and n[:-1] in names}  # closing-odds columns
            for row in rows:
                result = (row.get("FTR") or "").strip()
                pinnacle, closing = prices(row, "PS"), prices(row, "PSC")
                soft = {n: p for n, p in ((n, prices(row, n)) for n in sorted(names - NOT_BOOKMAKERS)) if p}
                if result not in KEYS or not pinnacle or len(soft) < 3:
                    continue
                try:
                    d = row["Date"].strip()
                    day = datetime.strptime(d, "%d/%m/%Y" if len(d.split("/")[-1]) == 4 else "%d/%m/%y").replace(tzinfo=timezone.utc)
                    books = {"PS": ("sharp", pinnacle), **{n: ("soft", p) for n, p in soft.items()}}
                    exchange = prices(row, "BFE") if "BFE" in names else None
                    if exchange:
                        books["BFE"] = ("exchange", exchange)
                    fairs, info = {}, {}
                    for name, (kind, p) in books.items():
                        margin = round(margin_percent([p[k] for k in KEYS]), 2)
                        info[name] = {"kind": kind, "margin": margin, "role": book_role(kind, margin), "commission": 0.0}
                        if info[name]["role"] != "ignored":
                            fairs[name] = fair(p)
                    cons = _consensus(fairs, info, KEYS)  # the app's recipe, including its guards
                    p_close = fair(closing) if closing else None
                    p_pinnacle = fair(pinnacle)
                except (OddsError, ValueError, KeyError):
                    continue
                soft_fair = {n: f for n, f in fairs.items() if info[n]["role"] == "soft"}
                mid = len(matches)
                matches.append({"day": day, "div": div, "top": div in TOP, "home": row.get("HomeTeam"), "away": row.get("AwayTeam")})
                for k in KEYS:
                    odds, book = max((p[k], n) for n, p in soft.items())
                    rest = [f for n, f in soft_fair.items() if n != book]
                    others = normalise({x: statistics.median(f[x] for f in rest) for x in KEYS}) if len(rest) >= MIN_OTHER_BOOKS else None
                    sels.append({
                        "m": mid, "sel": k, "p": cons["probs"][k], "p_pinnacle": p_pinnacle[k], "odds": odds, "book": book,
                        "ev": (cons["probs"][k] * odds - 1) * 100, "evx": (others[k] * odds - 1) * 100 if others else None,
                        "anchor": cons["anchor"], "won": result == k, "top": div in TOP,
                        "clv": (p_close[k] * odds - 1) * 100 if p_close else None,
                    })
    return sels, matches


def old_rule(s: dict) -> list[str]:
    """Rejection codes under the rule before v1.6 (empty list = qualifies)."""
    return [] if s["ev"] >= 3.0 else ["ev"]


def new_rule(s: dict) -> list[str]:
    """Rejection codes under the current settings, as in AnalysisService._qualify."""
    need = MIN_EV if s["anchor"] == "strong" else max(MIN_EV, MIN_EV_WEAK)
    codes = ["ev"] if s["ev"] < need else []
    if s["ev"] > 0 and s["evx"] is not None and s["evx"] < MIN_SOFT:
        codes.append("uncorroborated")
    return codes


def line(group: list[dict], label: str, n_matches: int = 0) -> None:
    n = len(group)
    if n < 5:
        print(f"  {label:44s} n={n}")
        return
    profit = [(s["odds"] - 1) if s["won"] else -1.0 for s in group]
    clv = [s["clv"] for s in group if s["clv"] is not None]
    ci = 1.96 * statistics.pstdev(profit) / math.sqrt(n) * 100
    per = f"{n / n_matches * 100:5.1f} per 100 matches" if n_matches else " " * 21
    print(f"  {label:44s} n={n:5d} {per} | claimed EV {statistics.mean(s['ev'] for s in group):+6.2f}%"
          f" | CLV {statistics.mean(clv):+5.2f}%, beat the close {100 * sum(c > 0 for c in clv) / len(clv):3.0f}%"
          f" | realised {sum(profit) / n * 100:+6.1f}% (+/-{ci:4.1f})")


def simulate(sels: list[dict], matches: list[dict], rule, label: str, top_only: bool) -> None:
    """Daily Picks with the app's own coupon builder, one run per match day."""
    by_day = defaultdict(list)
    for s in sels:
        if matches[s["m"]]["top"] or not top_only:
            by_day[matches[s["m"]]["day"]].append(s)
    counts, rows = Counter(), []
    for day, group in sorted(by_day.items()):
        analyses, lookup = {}, {}
        for s in group:
            m, fid = matches[s["m"]], f"m{s['m']}"
            a = analyses.setdefault(fid, {"fixture": {"id": fid, "home": m["home"], "away": m["away"], "sport": "football",
                                                    "kickoff": day.replace(hour=15).isoformat(), "status": "scheduled"},
                                       "competition": {"name": m["div"]}, "selections": []})
            risk = "medium" if s["p"] < 0.25 else "low"
            codes = rule(s)
            a["selections"].append({"market_id": "h2h", "market": "h2h", "line": None, "market_label": "1X2", "selection": s["sel"],
                                    "label": s["sel"], "best": {"bookmaker": s["book"], "name": s["book"], "odds": s["odds"], "source": "history"},
                                    "final_prob": s["p"], "fair_odds": 1 / s["p"], "ev_pct": s["ev"], "risk_rating": risk, "confidence": 74,
                                    "explanation": "", "pricing_flags": [], "rejection_codes": codes, "qualifies": not codes})
            lookup[(fid, s["sel"])] = s
        coupons = build_daily_picks(list(analyses.values()), dict(DEFAULT_SETTINGS), day.replace(hour=7))["coupons"]
        counts[len(coupons)] += 1
        for c in coupons:
            legs = [lookup[(leg["fixture_id"], leg["selection"])] for leg in c["legs"]]
            clv = math.prod((s["clv"] / 100 + 1) if s["clv"] is not None else 1.0 for s in legs)
            rows.append((c["combined_odds"], all(s["won"] for s in legs), (clv - 1) * 100))
    days, n = sum(counts.values()), len(rows)
    if not n:
        print(f"  {label:26s} match days {days}: no coupons")
        return
    profit = [(o - 1) if won else -1.0 for o, won, _ in rows]
    ci = 1.96 * statistics.pstdev(profit) / math.sqrt(n) * 100
    print(f"  {label:26s} match days {days} | at least one coupon on {100 * (days - counts[0]) / days:3.0f}% | {n / days:4.2f} coupons per day"
          f" | CLV {statistics.mean(r[2] for r in rows):+5.2f}% | realised {sum(profit) / n * 100:+5.1f}% (+/-{ci:.0f})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seasons", nargs="+", default=["2223", "2324", "2425", "2526"], help="e.g. 2425 2526")
    ap.add_argument("--refresh", action="store_true", help="download the files again")
    args = ap.parse_args()
    sels, matches = selections(args.seasons, args.refresh)
    if not sels:
        raise SystemExit("No data (no internet, or the seasons do not exist).")
    s = DEFAULT_SETTINGS
    picks = [x for x in sels if x["p"] >= s["min_leg_probability"] and x["odds"] <= s["max_leg_odds"]]
    n_top = sum(m["top"] for m in matches)
    groups = (("TOP 5 LEAGUES", [x for x in picks if x["top"]], n_top), ("12 OTHER LEAGUES", [x for x in picks if not x["top"]], len(matches) - n_top))
    print(f"{len(matches)} matches ({n_top} in the top 5 leagues), {min(m['day'] for m in matches):%Y-%m-%d} to {max(m['day'] for m in matches):%Y-%m-%d}")

    print("\n1. Is a claimed edge real?  Claimed EV against the closing line")
    for name, group, n in groups:
        print(f" {name}")
        for lo, hi in ((0, 1), (1, 1.5), (1.5, 2), (2, 3), (3, 4), (4, 6), (6, 8), (8, 12), (12, 999)):
            line([x for x in group if lo <= x["ev"] < hi], f"EV {lo}-{hi}%", n)

    print("\n2. Corroboration: claimed EV >= 1.5%, by EV against the OTHER bookmakers' fair price")
    for lo, hi in ((-99, -4), (-4, -2), (-2, -1), (-1, 0), (0, 2), (2, 5), (5, 999)):
        line([x for x in picks if x["ev"] >= 1.5 and x["evx"] is not None and lo <= x["evx"] < hi], f"against the others {lo}..{hi}%")

    print(f"\n3. The rule: before (EV >= 3%) and now (EV >= {MIN_EV}% with a sharp reference, {MIN_EV_WEAK}% without, others' fair price >= {MIN_SOFT}%)")
    for name, group, n in groups:
        print(f" {name}")
        line([x for x in group if not old_rule(x)], "before", n)
        line([x for x in group if not new_rule(x)], "now", n)

    print("\n4. Calibration of the fair probability (log-loss, lower is better)")
    for key, name in (("p", "BetFinder consensus"), ("p_pinnacle", "Pinnacle alone")):
        print(f"  {name:22s} {-sum(math.log(max(x[key], 1e-9)) for x in sels if x['won']) / len(matches):.5f}")

    print("\n5. Daily Picks with the app's coupon builder")
    for top_only, name in ((True, "TOP 5 LEAGUES"), (False, "ALL 17 LEAGUES")):
        print(f" {name}")
        simulate(sels, matches, old_rule, "before", top_only)
        simulate(sels, matches, new_rule, "now", top_only)


if __name__ == "__main__":
    main()
