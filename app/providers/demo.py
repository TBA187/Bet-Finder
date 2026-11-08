"""Demo data so the whole pipeline can be explored without API keys.

Everything here is FICTIONAL: invented teams ("Northbridge FC") and invented
bookmakers ("Demo Book A").  Every fixture is flagged ``is_demo`` and the UI
shows a banner.  Demo data is excluded from tracker statistics and removed
when demo mode is switched off.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from app.core.markets import market_id, selections_for
from app.core.models import Absence, Fixture, LineupInfo, Quote, TeamStats
from app.services.registry import Registry
from app.services.repository import Repository, fixture_id_for
from app.sports import model_for

DEMO_BOOKS = ["demo_sharp", "demo_book_a", "demo_book_b", "demo_book_c", "demo_book_d", "demo_book_e"]

# (sport, competition id, competition name, home, away, theta, hours from now, extra)
_FIXTURES = [
    ("football", "demo.football", "Demo Football League", "Northbridge FC", "Port Alden", {"lh": 1.65, "la": 1.05}, 3, {"misprice": ("h2h", None, "away", 0.12, "demo_book_c")}),
    ("football", "demo.football", "Demo Football League", "Riverside Athletic", "Kingsmoor United", {"lh": 1.30, "la": 1.25}, 4, {"stale": "demo_book_e"}),
    ("football", "demo.football", "Demo Football League", "Eastfield Rovers", "Hollow Bay", {"lh": 2.05, "la": 0.85}, 5, {"misprice": ("totals", 2.5, "over", 0.08, "demo_book_a"), "injuries": True}),
    ("football", "demo.football", "Demo Football League", "Westgate City", "Old Harbour", {"lh": 1.20, "la": 1.45}, 27, {}),
    ("basketball", "demo.basketball", "Demo Basketball League", "Capital Comets", "Lakeshore Lynx", {"mu": 4.5, "total": 164.0, "sd_m": 11.5, "sd_t": 15.5}, 4, {"misprice": ("h2h", None, "away", 0.10, "demo_book_b")}),
    ("basketball", "demo.basketball", "Demo Basketball League", "Ironvale Titans", "Summit Hawks", {"mu": -2.0, "total": 158.0, "sd_m": 11.5, "sd_t": 15.5}, 6, {}),
    ("handball", "demo.handball", "Demo Handball Cup", "Nordvik HK", "Aurora HC", {"mu": 2.0, "total": 58.0, "sd_m": 5.8, "sd_t": 6.2}, 3, {"misprice": ("h2h", None, "home", 0.09, "demo_book_d")}),
    ("handball", "demo.handball", "Demo Handball Cup", "Valemark HB", "Fjordby", {"mu": -1.0, "total": 56.0, "sd_m": 5.8, "sd_t": 6.2}, 5, {"suspension": True}),
]


def _margin_odds(probs: dict[str, float], margin: float, rng: random.Random, noise: float) -> dict[str, float]:
    out = {}
    for s, p in probs.items():
        noisy = max(0.01, p * (1 + rng.gauss(0, noise)))
        out[s] = noisy * (1 + margin)
    total = sum(out.values())
    scale = (1 + margin) / total
    return {s: round(max(1.02, 1 / (v * scale)), 2) for s, v in out.items()}


def _stats(rng: random.Random, team: str, attack: float, defence: float, games: int, split: float) -> TeamStats:
    recent = []
    for i in range(6):
        scored = max(0, round(rng.gauss(attack, attack ** 0.5)))
        conceded = max(0, round(rng.gauss(defence, defence ** 0.5)))
        recent.append({"date": f"2026-10-{10 + i:02d}", "venue": "H" if i % 2 else "A", "opponent": "Demo Opponent",
                       "scored": scored, "conceded": conceded})
    return TeamStats(team=team, source="demo", season="demo", games_home=games, games_away=games,
                     scored_home=round(attack * (1 + split), 2), conceded_home=round(defence * (1 - split), 2),
                     scored_away=round(attack * (1 - split), 2), conceded_away=round(defence * (1 + split), 2),
                     wins_home=games // 2, draws_home=games // 4, losses_home=games - games // 2 - games // 4,
                     wins_away=games // 3, draws_away=games // 4, losses_away=games - games // 3 - games // 4,
                     recent=recent)


def load_demo(repo: Repository, registry: Registry, now: datetime) -> int:
    repo.remove_demo_data()
    rng = random.Random(int(now.strftime("%Y%m%d")))
    count = 0
    for sport, cid, cname, home, away, theta, hours, extra in _FIXTURES:
        kickoff = (now + timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
        fx = Fixture(id=fixture_id_for(sport, home, away, kickoff), sport=sport, competition_id=cid, competition_name=cname,
                     home=home, away=away, kickoff=kickoff, is_demo=True, refs={"demo": f"{home}-{away}"})
        repo.save_fixture(fx)
        model = model_for(sport)
        mids = ["h2h", market_id("totals", 2.5 if sport == "football" else round(theta.get("total", 0) - 0.5) + 0.5)]
        if sport == "football":
            mids.append("btts")
        else:
            mids.append(market_id("spreads", -round(theta["mu"] - 0.5) - 0.5))
        quotes = []
        for mid in mids:
            market, line = (mid.split("@")[0], float(mid.split("@")[1])) if "@" in mid else (mid, None)
            probs = model.probabilities(theta, market, line)
            if not probs:
                continue
            for b_idx, book in enumerate(DEMO_BOOKS):
                margin = 0.025 if book == "demo_sharp" else rng.uniform(0.045, 0.075)
                noise = 0.01 if book == "demo_sharp" else 0.025
                odds = _margin_odds({s: probs[s] for s in selections_for(sport, market)}, margin, rng, noise)
                mis = extra.get("misprice")
                if mis and mis[0] == market and mis[1] == line and mis[4] == book:
                    odds[mis[2]] = round(odds[mis[2]] * (1 + mis[3]), 2)
                fetched = now - timedelta(minutes=8 if extra.get("stale") != book else 600)
                for s, o in odds.items():
                    opening = round(o * (1 + rng.uniform(-0.06, 0.06)), 2)
                    history = [(fetched - timedelta(hours=20), max(1.02, opening)), (fetched, o)]
                    quotes.append(Quote(fixture_id=fx.id, market=market, line=line, selection=s, bookmaker=book, odds=o,
                                        source="demo", fetched_at=fetched, history=history))
        repo.upsert_quotes(fx.id, quotes)
        # statistics consistent with the true parameters, with noise
        # Team averages that roughly reproduce the true parameters (venue split
        # removed first so the statistical model is not biased toward home).
        if sport == "football":
            split, games = 0.10, 7
            att_h, def_h = theta["lh"] / (1 + split), theta["la"] / (1 - split)
            att_a, def_a = theta["la"] / (1 - split), theta["lh"] / (1 + split)
        else:
            split, games = 0.015, 9
            half = theta["total"] / 2
            att_h, def_h = (half + theta["mu"] / 2) / (1 + split), (half - theta["mu"] / 2) / (1 - split)
            att_a, def_a = (half - theta["mu"] / 2) / (1 - split), (half + theta["mu"] / 2) / (1 + split)
        jitter = lambda v: v * (1 + rng.uniform(-0.05, 0.05))  # noqa: E731
        repo.save_team_stats(f"demo:{cid}:{home}", _stats(rng, home, jitter(att_h), jitter(def_h), games, split))
        repo.save_team_stats(f"demo:{cid}:{away}", _stats(rng, away, jitter(att_a), jitter(def_a), games, split))
        meetings = []
        for i in range(4):
            if sport == "football":
                h, a = max(0, round(rng.gauss(theta["lh"], 1))), max(0, round(rng.gauss(theta["la"], 1)))
            else:
                h = round(theta["total"] / 2 + theta["mu"] / 2 + rng.gauss(0, theta["sd_m"] / 2))
                a = round(theta["total"] / 2 - theta["mu"] / 2 + rng.gauss(0, theta["sd_m"] / 2))
            meetings.append({"date": f"202{5 - i // 2}-0{3 + i}-12", "home": home if i % 2 == 0 else away,
                             "away": away if i % 2 == 0 else home, "score_home": h if i % 2 == 0 else a,
                             "score_away": a if i % 2 == 0 else h})
        repo.save_h2h(fx.id, meetings)
        if sport == "football":
            # mimic a football injury feed: "no injuries reported" is real information
            repo.save_provider_news(fx.id, {"absences": [], "absences_source": "demo"})
        if extra.get("injuries"):
            repo.add_absence(fx.id, Absence(team="away", player="Demo Striker", kind="injury", importance="key", reason="hamstring", source="demo"))
            repo.add_absence(fx.id, Absence(team="away", player="Demo Midfielder", kind="doubtful", importance="regular", source="demo"))
        if extra.get("suspension"):
            repo.add_absence(fx.id, Absence(team="away", player="Demo Playmaker", kind="suspension", importance="key", reason="red card", source="demo"))
        if hours <= 3:
            repo.set_lineup(fx.id, "home", LineupInfo(status="expected", source="demo"))
        count += 1
    return count
