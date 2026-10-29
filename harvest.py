#!/usr/bin/env python3
"""2026 UK harvest reading. Yields versus the five-year average, not versus last year alone."""

from __future__ import annotations

import re

from http_fetch import fetch_bytes

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

AHDB = "https://ahdb.org.uk/news/final-harvest-report-confirms-cereal-yields-are-below-historic-averages-across-the-uk"
DEFRA = "https://www.gov.uk/government/statistics/cereal-and-oilseed-rape-production/provisional-cereal-and-oilseed-production-estimates-for-england-2026"

# Verified 29 Oct 2026. Used if a page will not parse.
FALLBACK = {
    "wheat_yield_t_ha": 6.9,
    "wheat_vs_five_year_pct": -11,
    "spring_barley_vs_five_year_pct": -17,
    "oats_vs_five_year_pct": -14,
    "osr_vs_five_year_pct": 19,
    "england_wheat_mt": 10.8,
    "england_wheat_vs_2025_pct": 0.8,
    "england_wheat_yield_t_ha": 7.2,
}


HOSTS = {"ahdb.org.uk", "www.gov.uk"}


def fetch(url: str) -> str:
    return fetch_bytes(
        url,
        allow_hosts=HOSTS,
        timeout=30,
        max_bytes=3_000_000,
        headers={"User-Agent": UA},
    ).decode("utf-8", "replace")


def deficit_score(pct_below: float) -> int:
    """pct_below is positive when the crop is short of its five-year average."""
    if pct_below <= 0:
        return 18
    return max(0, min(95, round(18 + pct_below * 3.6)))


def read() -> dict:
    notes = []
    wheat_vs = FALLBACK["wheat_vs_five_year_pct"]
    barley_vs = FALLBACK["spring_barley_vs_five_year_pct"]
    oats_vs = FALLBACK["oats_vs_five_year_pct"]
    osr_vs = FALLBACK["osr_vs_five_year_pct"]
    wheat_yield = FALLBACK["wheat_yield_t_ha"]
    source = "fallback verified 29 Oct 2026"
    try:
        text = fetch(AHDB)
        # "6.9 t/ha" ... "11% below the national five-year average"
        m = re.search(r"([0-9]+\.[0-9])\s*t/ha.*?([0-9]+)%\s+below the national five-year average", text, re.I | re.S)
        if m:
            wheat_yield = float(m.group(1))
            wheat_vs = -int(m.group(2))
            source = AHDB
        b = re.search(r"averaging\s+([0-9]+\.[0-9])\s*t/ha,\s+around\s+([0-9]+)%\s+below the five-year average", text, re.I)
        if b:
            barley_vs = -int(b.group(2))
        o = re.search(r"reaching\s+([0-9]+\.[0-9])\s*t/ha,\s+but remain around\s+([0-9]+)%\s+below", text, re.I)
        if o:
            oats_vs = -int(o.group(2))
        r = re.search(r"([0-9]+\.[0-9])\s*t/ha,\s+approximately\s+([0-9]+)%\s+above the UK five-year average", text, re.I)
        if r:
            osr_vs = int(r.group(2))
    except Exception as exc:
        notes.append(f"AHDB: {exc}")

    defra = {
        "england_wheat_mt": FALLBACK["england_wheat_mt"],
        "vs_2025_pct": FALLBACK["england_wheat_vs_2025_pct"],
        "yield_t_ha": FALLBACK["england_wheat_yield_t_ha"],
        "url": DEFRA,
    }
    try:
        page = fetch(DEFRA)
        m = re.search(
            r"2026 English wheat harvest is ([0-9]+\.[0-9]) million tonnes, an increase of ([0-9]+\.[0-9])%",
            page,
        )
        if m:
            defra["england_wheat_mt"] = float(m.group(1))
            defra["vs_2025_pct"] = float(m.group(2))
        y = re.search(r"overall yield increased by ([0-9]+\.[0-9])% to ([0-9]+\.[0-9]) tonnes per hectare", page)
        if y:
            defra["yield_change_pct"] = float(y.group(1))
            defra["yield_t_ha"] = float(y.group(2))
    except Exception as exc:
        notes.append(f"Defra: {exc}")

    wheat_score = deficit_score(-wheat_vs)
    barley_score = deficit_score(-barley_vs)
    oats_score = deficit_score(-oats_vs)
    # Wheat is the bread crop. Barley and oats still matter for feed and breakfast.
    score = round(wheat_score * 0.6 + barley_score * 0.25 + oats_score * 0.15)
    detail = (
        f"AHDB final 2026 survey: wheat {wheat_yield:.1f} t/ha, "
        f"{abs(wheat_vs):.0f}% below the five-year average. "
        f"Spring barley {abs(barley_vs):.0f}% below, oats {abs(oats_vs):.0f}% below. "
        f"Oilseed rape was {osr_vs:.0f}% above. "
        f"They put it down to limited rain from spring onwards, with huge farm-to-farm gaps. "
        f"Defra's England wheat tonnage is {defra['england_wheat_mt']} million tonnes, "
        f"{defra['vs_2025_pct']}% above 2025, but East of England and South East yields fell. "
        f"Up on last year is not the same as a normal crop. Full UK figures are due 10 December 2026."
    )
    return {
        "id": "harvest",
        "name": "This harvest",
        "score": score,
        "wheat_score": wheat_score,
        "barley_score": barley_score,
        "oats_score": oats_score,
        "wheat_yield_t_ha": wheat_yield,
        "wheat_vs_five_year_pct": wheat_vs,
        "spring_barley_vs_five_year_pct": barley_vs,
        "oats_vs_five_year_pct": oats_vs,
        "osr_vs_five_year_pct": osr_vs,
        "defra": defra,
        "source": source,
        "detail": detail,
        "notes": notes,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(read(), indent=2))
