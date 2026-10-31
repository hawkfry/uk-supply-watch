#!/usr/bin/env python3
"""Front month versus the next listed month.

Positive means the next month is dearer (contango, nearby not tight).
Negative means the front month is dearer (backwardation, nearby tight).
The spread is not a score.
"""

from __future__ import annotations

from datetime import date

MONTH_CODES = "FGHJKMNQUVXZ"
WHEAT_MONTHS = set("HKNUZ")
BRENT_MONTHS = set(MONTH_CODES)


def candidates(root: str, exchange: str, months: set[str], today: date, count: int = 8) -> list[str]:
    year, month = today.year, today.month
    out = []
    guard = 0
    while len(out) < count and guard < 36:
        code = MONTH_CODES[month - 1]
        if code in months:
            out.append(f"{root}{code}{year % 100:02d}.{exchange}")
        month += 1
        if month == 13:
            month = 1
            year += 1
        guard += 1
    return out


def spread_pct(front: float, nxt: float) -> float | None:
    if not front:
        return None
    return round((nxt - front) / front * 100, 1)


def pick_curve(quotes: list[dict], continuous: float | None) -> dict | None:
    """quotes are in expiry order. Front is the first price near the continuous print."""
    live = [row for row in quotes if row.get("price")]
    if len(live) < 2:
        return None
    start = 0
    if continuous:
        for i, row in enumerate(live):
            if abs(row["price"] - continuous) / continuous <= 0.015:
                start = i
                break
        else:
            return None
    if start + 1 >= len(live):
        return None
    front, nxt = live[start], live[start + 1]
    pct = spread_pct(front["price"], nxt["price"])
    if pct is None:
        return None
    return {
        "front": front["symbol"],
        "next": nxt["symbol"],
        "front_price": round(front["price"], 2),
        "next_price": round(nxt["price"], 2),
        "curve_pct": pct,
        "as_of": front.get("as_of") or nxt.get("as_of"),
    }


def phrase(curve_pct: float | None) -> str:
    if curve_pct is None:
        return ""
    if abs(curve_pct) < 0.4:
        return "front flat vs next"
    gap = f"{abs(curve_pct):.1f}%"
    if curve_pct < 0:
        return f"front {gap} dearer"
    return f"front {gap} cheaper"
