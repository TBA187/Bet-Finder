"""Bookmaker odds comparison for identical matches and markets.

For every market of a fixture this builds:
* the price of each selection at every bookmaker,
* each bookmaker's margin (overround),
* a margin-free consensus probability (sharp bookmakers weighted highest,
  otherwise the median of all complete bookmaker markets),
* best price, second-best price and median price per selection,
* flags for unusually high prices, possible pricing errors, stale prices
  and cross-bookmaker arbitrage.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Optional

from app.core.markets import market_label, parse_market_id, selection_label, selections_for
from app.core.models import Fixture, Quote
from app.core.odds_math import arbitrage_percent, devig, margin_percent, normalise
from app.i18n import lang_of
from app.services.registry import Registry

SHARP_WEIGHT = {"sharp": 1.0, "exchange": 0.8}


def latest_quotes(quotes: list[Quote]) -> dict[str, dict[str, dict[str, Quote]]]:
    """market_id -> bookmaker -> selection -> freshest quote (across sources)."""
    out: dict[str, dict[str, dict[str, Quote]]] = {}
    for q in quotes:
        sel_map = out.setdefault(q.market_id, {}).setdefault(q.bookmaker, {})
        cur = sel_map.get(q.selection)
        if cur is None or q.fetched_at > cur.fetched_at:
            sel_map[q.selection] = q
    return out


def _age_minutes(q: Quote, now: datetime) -> float:
    ref = q.book_updated_at or q.fetched_at
    return max(0.0, (now - ref).total_seconds() / 60.0)


def compare_market(fixture: Fixture, mid: str, books: dict[str, dict[str, Quote]], registry: Registry,
                   settings: dict, now: datetime, include_history: bool = False) -> Optional[dict]:
    market, line = parse_market_id(mid)
    try:
        sels = selections_for(fixture.sport, market)
    except ValueError:
        return None
    stale_after = float(settings.get("stale_minutes", 240))
    method = settings.get("devig_method", "shin")

    book_rows = []
    fair_by_book: dict[str, dict[str, float]] = {}
    for bk, sel_quotes in books.items():
        prices = {s: sel_quotes[s].odds for s in sels if s in sel_quotes}
        if not prices:
            continue
        complete = len(prices) == len(sels)
        ages = [_age_minutes(sel_quotes[s], now) for s in prices]
        stale = max(ages) > stale_after
        meta = registry.bookmaker(bk)
        row = {
            "bookmaker": bk,
            "name": meta.get("name", bk),
            "type": meta.get("type", "soft"),
            "source": sorted({sel_quotes[s].source for s in prices}),
            "prices": prices,
            "complete": complete,
            "margin_pct": round(margin_percent([prices[s] for s in sels]), 2) if complete else None,
            "age_min": round(min(ages), 1),
            "stale": stale,
        }
        if complete and not stale:
            fair_by_book[bk] = dict(zip(sels, devig([prices[s] for s in sels], method)))
        book_rows.append(row)
    if not book_rows:
        return None

    consensus = _consensus(fair_by_book, registry, sels)
    probs = consensus["probs"]

    # per-selection quote views, best prices, flags
    best: dict[str, dict] = {}
    quote_views: dict[str, list[dict]] = {}
    suspicious = float(settings.get("suspicious_ev_pct", 20.0)) / 100.0
    for s in sels:
        entries = [(bk, books[bk][s]) for bk in books if s in books[bk]]
        if not entries:
            continue
        entries.sort(key=lambda e: e[1].odds, reverse=True)
        all_odds = [q.odds for _, q in entries]
        views = []
        for bk, q in entries:
            others = [q2.odds for b2, q2 in entries if b2 != bk]
            med_others = median(others) if len(others) >= 2 else None
            dev = (q.odds / med_others - 1.0) if med_others else None
            edge = (q.odds * probs[s] - 1.0) if probs else None
            age = _age_minutes(q, now)
            flags = []
            if dev is not None and dev >= 0.05:
                flags.append("high_price")
            if dev is not None and dev >= 0.10:
                flags.append("outlier")
            if edge is not None and dev is not None and edge >= suspicious and dev >= 0.10:
                flags.append("possible_error")
            if age > stale_after:
                flags.append("stale")
            if q.source == "manual":
                flags.append("manual")
            if q.source == "scraper":
                flags.append("scraped")
            view = {
                "bookmaker": bk,
                "name": registry.bookmaker_name(bk),
                "odds": q.odds,
                "implied_pct": round(100.0 / q.odds, 2),
                "dev_vs_median_pct": round(dev * 100, 2) if dev is not None else None,
                "edge_vs_fair_pct": round(edge * 100, 2) if edge is not None else None,
                "source": q.source,
                "age_min": round(age, 1),
                "flags": flags,
                "opening_odds": q.history[0][1] if q.history else q.odds,
                "move_pct": round((q.odds / q.history[0][1] - 1) * 100, 2) if q.history else 0.0,
            }
            if include_history:
                view["history"] = [[t.isoformat(), o] for t, o in q.history]
            views.append(view)
        quote_views[s] = views
        best[s] = {
            "bookmaker": entries[0][0],
            "name": registry.bookmaker_name(entries[0][0]),
            "odds": entries[0][1].odds,
            "second_odds": all_odds[1] if len(all_odds) > 1 else None,
            "median_odds": round(median(all_odds), 3),
            "n_quotes": len(all_odds),
        }

    arb = None
    if len(best) == len(sels):
        arb = arbitrage_percent([best[s]["odds"] for s in sels])
    margins = [r["margin_pct"] for r in book_rows if r["margin_pct"] is not None]
    return {
        "market_id": mid,
        "market": market,
        "line": line,
        "label": market_label(fixture.sport, market, line, lang_of(settings)),
        "selections": list(sels),
        "selection_labels": {s: selection_label(fixture.sport, market, line, s, fixture.home, fixture.away, lang_of(settings))
                             for s in sels},
        "books": sorted(book_rows, key=lambda r: (r["type"] not in ("sharp", "exchange"), r["name"].lower())),
        "consensus": consensus,
        "best": best,
        "quotes": quote_views,
        "arbitrage_pct": round(arb, 2) if arb is not None else None,
        "avg_margin_pct": round(sum(margins) / len(margins), 2) if margins else None,
    }


def _consensus(fair_by_book: dict[str, dict[str, float]], registry: Registry, sels) -> dict:
    if not fair_by_book:
        return {"probs": None, "method": None, "n_books": 0, "sharp_books": [], "dispersion": None}
    sharp = {bk: p for bk, p in fair_by_book.items() if registry.is_sharp(bk)}
    soft = {bk: p for bk, p in fair_by_book.items() if bk not in sharp}

    def med(group):
        return normalise({s: median(p[s] for p in group.values()) for s in sels})

    if sharp:
        weights = {bk: SHARP_WEIGHT.get(registry.bookmaker(bk).get("type"), 0.8) for bk in sharp}
        wsum = sum(weights.values())
        sharp_probs = normalise({s: sum(sharp[bk][s] * weights[bk] for bk in sharp) / wsum for s in sels})
        if soft:
            soft_probs = med(soft)
            probs = normalise({s: 0.75 * sharp_probs[s] + 0.25 * soft_probs[s] for s in sels})
            method = "sharp-weighted"
        else:
            probs, method = sharp_probs, "sharp"
    elif len(soft) == 1:
        probs, method = next(iter(soft.values())), "single-book"
    else:
        probs, method = med(soft), "median"
    dispersion = {s: round(max(p[s] for p in fair_by_book.values()) - min(p[s] for p in fair_by_book.values()), 4) for s in sels}
    return {
        "probs": {s: round(v, 5) for s, v in probs.items()},
        "method": method,
        "n_books": len(fair_by_book),
        "sharp_books": sorted(sharp),
        "dispersion": dispersion,
    }


def compare_fixture(fixture: Fixture, quotes: list[Quote], registry: Registry, settings: dict, now: datetime,
                    include_history: bool = False) -> dict[str, dict]:
    grouped = latest_quotes(quotes)
    out = {}
    for mid, books in grouped.items():
        comp = compare_market(fixture, mid, books, registry, settings, now, include_history)
        if comp:
            out[mid] = comp
    return out
