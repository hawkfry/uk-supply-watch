#!/usr/bin/env python3
"""Refresh the watch and print a short digest. Used by the morning cron."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/jamiefry/uk-supply-watch")
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
    if d.get("errors"):
        print("Feeds that failed: " + "; ".join(d["errors"]))
    print("Open on your phone: ", end="")
    url_file = ROOT / "data" / "phone-url.txt"
    if url_file.exists():
        print(url_file.read_text().strip())
    else:
        print("http://127.0.0.1:8765 on this Mac.")
    if proc.returncode:
        print("Refresh exited", proc.returncode)
        return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
