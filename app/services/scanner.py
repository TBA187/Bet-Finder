"""Bookmaker odds comparison for identical matches and markets.

For every market of a fixture this builds:
* the price of each selection at every bookmaker (exchange prices also after commission),
* each bookmaker's margin (overround),
* a margin-free consensus probability (sharp bookmakers weighted highest,
  otherwise the median of all complete bookmaker markets),
* best price, second-best price and median price per selection,
* flags for unusually high prices, possible pricing errors, stale prices
  and cross-bookmaker arbitrage.

Two rules keep the consensus honest:
* A sharp bookmaker or an exchange is only the reference price ("anchor") when
  its own market is tight and it does not contradict every other bookmaker.
  An exchange without liquidity shows prices that nobody can actually bet.
* Ice hockey: a bookmaker that prices the 3-way regulation-time result also
  prices totals and handicaps for regulation time.  That is a different bet
  from the overtime-inclusive price of other bookmakers, so the two are never
  compared with each other.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Optional

from app.core.markets import market_label, parse_market_id, selection_label, selections_for
from app.core.models import Fixture, Quote
from app.core.odds_math import devig, margin_percent, normalise
from app.i18n import lang_of
from app.services.registry import Registry

SHARP_WEIGHT = {"sharp": 1.0, "exchange": 0.8}
# Overround (%) up to which a sharp bookmaker / an exchange is trusted as the reference price.
ANCHOR_MAX_MARGIN = {"sharp": 8.0, "exchange": 5.0}
STRONG_EXCHANGE_MARGIN = 2.5   # an exchange this tight is as good a reference as a sharp bookmaker
DEAD_MARGIN = 12.0             # an exchange quoting more than this has no real market: ignore it
ANCHOR_MAX_GAP = 0.08          # an anchor this far (probability) from all other bookmakers is no reference
MIN_OTHER_BOOKS = 3            # bookmakers needed to judge an anchor, or to corroborate a price
REGULATION_SPORTS = ("icehockey",)


def latest_quotes(quotes: list[Quote]) -> dict[str, dict[str, dict[str, Quote]]]:
    """market_id -> bookmaker -> selection -> freshest quote (across sources)."""
    out: dict[str, dict[str, dict[str, Quote]]] = {}
    for q in quotes:
        sel_map = out.setdefault(q.market_id, {}).setdefault(q.bookmaker, {})
        cur = sel_map.get(q.selection)
        if cur is None or q.fetched_at > cur.fetched_at:
            sel_map[q.selection] = q
    return out


def market_books(fixture: Fixture, quotes: list[Quote]) -> tuple[dict[str, dict[str, dict[str, Quote]]], dict[str, list[str]]]:
    """``latest_quotes`` with regulation-time prices kept apart from overtime-inclusive ones.

    Returns (market_id -> bookmaker -> selection -> quote, market_id -> bookmakers left out).
    A market where every bookmaker prices regulation time is consistent and stays as it is."""
    grouped = latest_quotes(quotes)
    excluded: dict[str, list[str]] = {}
    if fixture.sport in REGULATION_SPORTS and "h2h3" in grouped:
        regulation = set(grouped["h2h3"]) - set(grouped.get("h2h", {}))
        for mid, books in grouped.items():
            if parse_market_id(mid)[0] not in ("totals", "spreads"):
                continue
            mixed = regulation & set(books)
            if mixed and set(books) - regulation:
                excluded[mid] = sorted(mixed)
                for bk in mixed:
                    del books[bk]
    return grouped, excluded


def book_role(kind: str, margin_pct: Optional[float]) -> str:
    """anchor | soft | ignored - from the bookmaker type and its own overround in this market."""
    if kind == "exchange":
        if margin_pct is None or not (-2.0 <= margin_pct <= DEAD_MARGIN):
            return "ignored"  # one-sided, crossed or very wide: no liquidity behind these prices
        return "anchor" if margin_pct <= ANCHOR_MAX_MARGIN["exchange"] else "soft"
    if kind == "sharp" and margin_pct is not None and margin_pct <= ANCHOR_MAX_MARGIN["sharp"]:
        return "anchor"
    return "soft"


def net_odds(odds: float, commission_pct: float) -> float:
    """What a winning bet really pays after the exchange's commission on winnings."""
    if not commission_pct:
        return odds
    return 1.0 + (odds - 1.0) * (1.0 - commission_pct / 100.0)


def _commission(meta: dict) -> float:
    try:
        value = float(meta.get("commission_pct") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return value if 0.0 < value < 50.0 else 0.0


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
    info: dict[str, dict] = {}
    for bk, sel_quotes in books.items():
        prices = {s: sel_quotes[s].odds for s in sels if s in sel_quotes}
        if not prices:
            continue
        complete = len(prices) == len(sels)
        ages = [_age_minutes(sel_quotes[s], now) for s in prices]
        stale = max(ages) > stale_after
        meta = registry.bookmaker(bk)
        kind = meta.get("type", "soft")
        margin = round(margin_percent([prices[s] for s in sels]), 2) if complete else None
        info[bk] = {"kind": kind, "margin": margin, "role": book_role(kind, margin), "commission": _commission(meta)}
        row = {
            "bookmaker": bk,
            "name": meta.get("name", bk),
            "type": kind,
            "source": sorted({sel_quotes[s].source for s in prices}),
            "prices": prices,
            "complete": complete,
            "margin_pct": margin,
            "age_min": round(min(ages), 1),
            "stale": stale,
            "commission_pct": info[bk]["commission"] or None,
        }
        if complete and not stale and info[bk]["role"] != "ignored":
            fair_by_book[bk] = dict(zip(sels, devig([prices[s] for s in sels], method)))
        book_rows.append(row)
    if not book_rows:
        return None

    consensus = _consensus(fair_by_book, info, sels)  # may still drop an anchor that contradicts everyone else
    probs = consensus["probs"]
    for row in book_rows:
        row["role"] = info[row["bookmaker"]]["role"]

    # Corroboration: what do the OTHER ordinary bookmakers say this selection is worth?
    soft_fair = {bk: p for bk, p in fair_by_book.items() if info[bk]["role"] == "soft"}
    others_cache: dict[str, Optional[dict[str, float]]] = {}

    def others(bk: str) -> Optional[dict[str, float]]:
        if bk not in others_cache:
            rest = [p for b, p in soft_fair.items() if b != bk]
            others_cache[bk] = (normalise({s: median(p[s] for p in rest) for s in sels})
                                if len(rest) >= MIN_OTHER_BOOKS else None)
        return others_cache[bk]

    # per-selection quote views, best prices, flags
    best: dict[str, dict] = {}
    quote_views: dict[str, list[dict]] = {}
    suspicious = float(settings.get("suspicious_ev_pct", 20.0)) / 100.0
    for s in sels:
        # prices of an ignored exchange cannot really be bet, so they are never offered as a price
        entries = [(bk, books[bk][s], net_odds(books[bk][s].odds, info[bk]["commission"]))
                   for bk in books if bk in info and s in books[bk] and info[bk]["role"] != "ignored"]
        if not entries:
            continue
        entries.sort(key=lambda e: e[2], reverse=True)
        views = []
        for bk, q, net in entries:
            rest = [n for b2, _q, n in entries if b2 != bk]
            med_others = median(rest) if len(rest) >= 2 else None
            dev = (net / med_others - 1.0) if med_others else None
            edge = (net * probs[s] - 1.0) if probs else None
            other = others(bk)
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
                "net_odds": round(net, 4),
                "commission_pct": info[bk]["commission"] or None,
                "implied_pct": round(100.0 / q.odds, 2),
                "dev_vs_median_pct": round(dev * 100, 2) if dev is not None else None,
                "edge_vs_fair_pct": round(edge * 100, 2) if edge is not None else None,
                "edge_vs_others_pct": round((net * other[s] - 1.0) * 100, 2) if other else None,
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
            "net_odds": round(entries[0][2], 4),
            "second_odds": entries[1][1].odds if len(entries) > 1 else None,
            "median_odds": round(median(q.odds for _bk, q, _net in entries), 3),
            "n_quotes": len(entries),
        }

    arb = None
    if len(best) == len(sels):
        inv = sum(1.0 / best[s]["net_odds"] for s in sels)  # after commission: what can really be locked in
        arb = (1.0 / inv - 1.0) * 100.0 if inv < 1.0 else None
    margins = [r["margin_pct"] for r in book_rows if r["margin_pct"] is not None and r["role"] != "ignored"]
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
        "excluded_books": [],
    }


def _consensus(fair_by_book: dict[str, dict[str, float]], info: dict[str, dict], sels) -> dict:
    """Fair probabilities: 75% anchors (sharp bookmakers, liquid exchanges) + 25% median of the others.

    ``info[bookmaker]["role"]`` is updated when an anchor turns out to contradict every other bookmaker."""
    empty = {"probs": None, "method": None, "n_books": 0, "sharp_books": [], "anchor": None, "dispersion": None}
    if not fair_by_book:
        return empty

    def med(group):
        return normalise({s: median(p[s] for p in group.values()) for s in sels})

    anchors = {bk: p for bk, p in fair_by_book.items() if info[bk]["role"] == "anchor"}
    soft = {bk: p for bk, p in fair_by_book.items() if info[bk]["role"] == "soft"}
    if anchors and len(soft) >= MIN_OTHER_BOOKS:
        reference = med(soft)
        for bk in list(anchors):
            if max(abs(anchors[bk][s] - reference[s]) for s in sels) > ANCHOR_MAX_GAP:
                p = anchors.pop(bk)
                if info[bk]["kind"] == "exchange":
                    info[bk]["role"] = "ignored"  # an exchange far from everyone: stale or empty order book
                else:
                    info[bk]["role"] = "soft"     # still a price, but not the reference
                    soft[bk] = p
    used = {**anchors, **soft}
    if not used:
        return empty
    if anchors:
        weights = {bk: SHARP_WEIGHT.get(info[bk]["kind"], 0.8) for bk in anchors}
        wsum = sum(weights.values())
        sharp_probs = normalise({s: sum(anchors[bk][s] * weights[bk] for bk in anchors) / wsum for s in sels})
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
    strong = any(info[bk]["kind"] == "sharp" or info[bk]["margin"] <= STRONG_EXCHANGE_MARGIN for bk in anchors)
    dispersion = {s: round(max(p[s] for p in used.values()) - min(p[s] for p in used.values()), 4) for s in sels}
    return {
        "probs": {s: round(v, 5) for s, v in probs.items()},
        "method": method,
        "n_books": len(used),
        "sharp_books": sorted(anchors),
        "anchor": "strong" if strong else "weak" if anchors else None,
        "dispersion": dispersion,
    }


def compare_fixture(fixture: Fixture, quotes: list[Quote], registry: Registry, settings: dict, now: datetime,
                    include_history: bool = False) -> dict[str, dict]:
    grouped, excluded = market_books(fixture, quotes)
    out = {}
    for mid, books in grouped.items():
        comp = compare_market(fixture, mid, books, registry, settings, now, include_history) if books else None
        if comp:
            comp["excluded_books"] = [{"bookmaker": bk, "name": registry.bookmaker_name(bk), "reason": "regulation_time"}
                                      for bk in excluded.get(mid, [])]
            out[mid] = comp
    return out
