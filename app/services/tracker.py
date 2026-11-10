"""Bet tracker with automatic settlement and Closing Line Value (CLV).

CLV compares the odds you took with the margin-free consensus probability at
kick-off (built from the stored price history):

    CLV % = (odds taken x closing fair probability - 1) x 100

Beating the closing line consistently is the best available evidence that the
value detection works.  Nothing here is invented: every record comes from a
selection you chose to track.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from app.core.models import Quote, utcnow
from app.services.registry import Registry
from app.services.repository import Repository
from app.services.scanner import compare_market, market_books
from app.sports.settle import outcome, payout

STATUS_FROM_OUTCOME = {"win": "won", "lose": "lost", "push": "void", "half_win": "half_won", "half_lose": "half_lost"}


class TrackerError(ValueError):
    pass


class TrackerService:
    FILE = "bets"

    def __init__(self, repo: Repository, registry: Registry):
        self.repo = repo
        self.registry = registry

    def _items(self) -> list[dict]:
        data = self.repo.store.read(self.FILE, [])
        return data if isinstance(data, list) else []

    @staticmethod
    def _mine(item: dict, owner: Optional[str], legacy: bool) -> bool:
        """owner=None means everything (scripts, tests); otherwise one account's bets,
        plus bets from before accounts existed when ``legacy`` is set."""
        if owner is None:
            return True
        return item.get("owner") == owner or (legacy and not item.get("owner"))

    # ----------------------------------------------------------------- create
    def track(self, legs: list[dict], *, title: str, stake: float, source: str, note: str = "",
              owner: Optional[str] = None) -> dict:
        if not legs:
            raise TrackerError("Nothing to track")
        if len({leg["fixture_id"] for leg in legs}) != len(legs):
            raise TrackerError("A coupon cannot contain two selections from the same match")
        if stake <= 0:
            raise TrackerError("Stake must be positive")
        clean = []
        for leg in legs:
            fx = self.repo.fixture(leg["fixture_id"])
            if not fx:
                raise TrackerError(f"Unknown fixture {leg['fixture_id']}")
            if fx.kickoff <= utcnow():
                raise TrackerError(f"{fx.name} has already started; only pre-match prices can be tracked")
            clean.append({
                "fixture_id": fx.id, "match": fx.name, "sport": fx.sport, "competition": fx.competition_name,
                "kickoff": fx.kickoff.isoformat(),
                "market": leg["market"], "line": leg.get("line"), "market_id": leg["market_id"],
                "market_label": leg.get("market_label", leg["market_id"]),
                "selection": leg["selection"], "selection_label": leg.get("selection_label", leg["selection"]),
                "bookmaker": leg["bookmaker"], "bookmaker_name": self.registry.bookmaker_name(leg["bookmaker"]),
                "odds": float(leg["odds"]), "probability": leg.get("probability"), "fair_odds": leg.get("fair_odds"),
                "ev_pct": leg.get("ev_pct"),
                "result": None, "closing_odds": None, "closing_fair_prob": None, "clv_pct": None,
            })
        total = 1.0
        for leg in clean:
            total *= leg["odds"]
        item = {
            "id": uuid.uuid4().hex[:12], "created_at": utcnow().isoformat(), "kind": "coupon" if len(clean) > 1 else "single",
            "title": title, "stake": float(stake), "source": source, "note": note,
            "legs": clean, "combined_odds": round(total, 3),
            "status": "pending", "return": None, "profit": None, "manual_result": False, "owner": owner,
        }
        with self.repo.store.edit(self.FILE, []) as data:
            data.append(item)
        return item

    def delete(self, item_id: str, owner: Optional[str] = None, legacy: bool = False) -> bool:
        with self.repo.store.edit(self.FILE, []) as data:
            before = len(data)
            data[:] = [i for i in data if not (i.get("id") == item_id and self._mine(i, owner, legacy))]
            return len(data) != before

    def settle_manual(self, item_id: str, status: str, owner: Optional[str] = None, legacy: bool = False) -> dict:
        if status not in ("won", "lost", "void", "pending"):
            raise TrackerError("status must be won, lost, void or pending")
        with self.repo.store.edit(self.FILE, []) as data:
            for item in data:
                if item.get("id") == item_id and self._mine(item, owner, legacy):
                    item["status"] = status
                    item["manual_result"] = status != "pending"
                    mult = {"won": item["combined_odds"], "lost": 0.0, "void": 1.0}.get(status)
                    item["return"] = round(item["stake"] * mult, 2) if mult is not None else None
                    item["profit"] = round(item["return"] - item["stake"], 2) if mult is not None else None
                    return item
        raise TrackerError("Unknown tracked item")

    # ----------------------------------------------------------------- update
    def refresh(self, settings: dict) -> None:
        """Settle finished fixtures and compute CLV for started ones."""
        now = utcnow()
        changed = False
        items = self._items()
        fixtures: Optional[dict] = None  # loaded once, and only when a leg still needs work
        for item in items:
            if item.get("manual_result"):
                continue
            for leg in item["legs"]:
                if leg["result"] is not None and leg["clv_pct"] is not None:
                    continue
                if fixtures is None:
                    fixtures = {f.id: f for f in self.repo.fixtures()}
                fx = fixtures.get(leg["fixture_id"])
                if not fx:
                    continue
                if leg["clv_pct"] is None and fx.kickoff <= now:
                    clv = self._closing(leg, fx.kickoff, fx, settings)
                    if clv:
                        leg.update(clv)
                        changed = True
                if leg["result"] is None and fx.status == "finished" and fx.score_home is not None:
                    leg["result"] = outcome(leg["market"], leg["line"], leg["selection"], fx.score_home, fx.score_away)
                    changed = True
                elif leg["result"] is None and fx.status == "cancelled":
                    leg["result"] = "push"
                    changed = True
            if all(leg["result"] is not None for leg in item["legs"]) and item["status"] == "pending":
                mult = 1.0
                for leg in item["legs"]:
                    mult *= payout(leg["result"], leg["odds"])
                item["return"] = round(item["stake"] * mult, 2)
                item["profit"] = round(item["return"] - item["stake"], 2)
                if len(item["legs"]) == 1:
                    item["status"] = STATUS_FROM_OUTCOME[item["legs"][0]["result"]]
                else:
                    item["status"] = "won" if mult > 1 else "lost" if mult == 0 else "void" if mult == 1 else "partial"
                changed = True
        if changed:
            with self.repo.store.edit(self.FILE, []) as data:
                by_id = {i["id"]: i for i in items}
                data[:] = [by_id.get(i.get("id"), i) for i in data]

    def _closing(self, leg: dict, kickoff: datetime, fx, settings: dict) -> Optional[dict]:
        snapshot = []
        for q in self.repo.quotes(leg["fixture_id"]):  # every market: the result markets tell which rules a bookmaker uses
            past = [(t, o) for t, o in q.history if t <= kickoff]
            if not past:
                continue
            t, o = past[-1]
            snapshot.append(Quote(**{**q.model_dump(), "odds": o, "fetched_at": t, "book_updated_at": None, "history": []}))
        if not snapshot:
            return None
        books = market_books(fx, snapshot)[0].get(leg["market_id"], {})
        comp = (compare_market(fx, leg["market_id"], books, self.registry, {**settings, "stale_minutes": 10 ** 6}, kickoff)
                if books else None)
        out: dict = {}
        own = books.get(leg["bookmaker"], {}).get(leg["selection"])
        if own:
            out["closing_odds"] = own.odds
        probs = comp["consensus"]["probs"] if comp else None
        if probs and leg["selection"] in probs:
            p = probs[leg["selection"]]
            out["closing_fair_prob"] = round(p, 4)
            out["clv_pct"] = round((leg["odds"] * p - 1) * 100, 2)
        return out or None

    # ------------------------------------------------------------------ read
    def overview(self, settings: dict, owner: Optional[str] = None, legacy: bool = False) -> dict:
        self.refresh(settings)
        items = sorted((i for i in self._items() if self._mine(i, owner, legacy)),
                       key=lambda i: i.get("created_at", ""), reverse=True)
        settled = [i for i in items if i["status"] != "pending" and i.get("profit") is not None]
        staked = sum(i["stake"] for i in settled)
        profit = sum(i["profit"] for i in settled)
        legs = [leg for i in items for leg in i["legs"]]
        clvs = [leg["clv_pct"] for leg in legs if leg.get("clv_pct") is not None]
        evs = [leg["ev_pct"] for leg in legs if leg.get("ev_pct") is not None]
        wins = sum(1 for i in settled if i["status"] in ("won", "half_won"))
        return {
            "items": items,
            "summary": {
                "tracked": len(items), "settled": len(settled), "pending": sum(1 for i in items if i["status"] == "pending"),
                "won": wins, "hit_rate_pct": round(100 * wins / len(settled), 1) if settled else None,
                "staked": round(staked, 2), "profit": round(profit, 2),
                "roi_pct": round(100 * profit / staked, 2) if staked else None,
                "avg_ev_pct": round(sum(evs) / len(evs), 2) if evs else None,
                "avg_clv_pct": round(sum(clvs) / len(clvs), 2) if clvs else None,
                "clv_beat_rate_pct": round(100 * sum(1 for c in clvs if c > 0) / len(clvs), 1) if clvs else None,
                "clv_samples": len(clvs),
            },
        }

    def referenced_fixtures(self) -> set[str]:
        return {leg["fixture_id"] for i in self._items() for leg in i.get("legs", [])}
