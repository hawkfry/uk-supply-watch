#!/usr/bin/env python3
"""Refresh the watch and print a short digest. Used by the morning cron."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "latest.json"


def main() -> int:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "refresh.py")],
        capture_output=True,
        text=True,
    )
    if not DATA.exists():
        print("UK supply watch failed to write data.")
        if proc.stderr:
            print(proc.stderr[-500:])
        return 1
    d = json.loads(DATA.read_text())
    food = d.get("food") or {}
    fuel = d.get("fuel") or {}
    pumps = (d.get("pumps") or {}).get("latest") or {}
    print(f"UK supply watch · {d.get('as_of', '')}")
    print(f"Food {food.get('score', '—')} {food.get('band', '')}. {food.get('why', '')}")
    print(f"Fuel prices {fuel.get('score', '—')} {fuel.get('band', '')}. {fuel.get('why', '')}")
    spare = d.get("spare") or {}
    if spare.get("score") is not None:
        print(f"Stocks and spare {spare.get('score')} {spare.get('band', '')}. {spare.get('why', '')}")
    shelf = d.get("shelf") or {}
    if shelf.get("retailers"):
        bits = []
        for row in shelf["retailers"]:
            if row.get("listed"):
                bits.append(f"{row['retailer']} {row['available']}/{row['listed']}")
        if bits:
            print("Eggs on the shelf: " + ", ".join(bits) + ".")
    if pumps:
        print(f"Pumps week of {pumps.get('date')}: petrol {pumps.get('petrol')}p, diesel {pumps.get('diesel')}p.")
    grain = d.get("grain") or {}
    wheat = grain.get("wheat") or {}
    if wheat.get("stocks_use_pct") is not None:
        change = wheat.get("change_pts")
        moved = "no month change" if change is None else f"{change:+.1f} pts on the month"
        print(f"World wheat stocks {wheat['stocks_use_pct']}% of use, {grain.get('report')}, {moved}. Not in the food score.")
    markets = d.get("markets") or {}
    for key, label in (("brent", "Brent"), ("wheat", "Wheat")):
        row = markets.get(key) or {}
        if row.get("curve_label"):
            print(f"{label} nearby: {row['curve_label']}, settle {row.get('curve_as_of')}. Not a score.")
    if d.get("errors"):
        print("Feeds that failed: " + "; ".join(d["errors"]))
    print("Open on your phone: https://hawkfry.github.io/uk-supply-watch/")
    if proc.returncode:
        print("Refresh exited", proc.returncode)
        return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
