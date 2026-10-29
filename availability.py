#!/usr/bin/env python3
"""Public grocery pages that expose price and availability.

Polite: a handful of pages, once a day. No login, no basket, no bypass.
Ocado and Morrisons put product availability in the search HTML.
Trolley lists which supermarkets currently show a price.
"""

from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import quote

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

NOT_SHELL = (
    "scotch", "custard", "quorn", "chocolate", "noodle", "mayo",
    "sandwich", "cake", "brownie", "tart", "pasta", "noodle",
    "replacement", "replac", "vegan egg", "egg white", "liquid egg",
)


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def is_shell_egg(name: str) -> bool:
    n = name.lower()
    if "egg" not in n:
        return False
    return not any(bad in n for bad in NOT_SHELL)


def parse_product_entities(html: str) -> list[dict]:
    key = '"productEntities":'
    i = html.find(key)
    if i < 0:
        return []
    start = i + len(key)
    while start < len(html) and html[start] in " \n":
        start += 1
    if start >= len(html) or html[start] != "{":
        return []
    depth = 0
    end = start
    for j, ch in enumerate(html[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    try:
        data = json.loads(html[start:end])
    except json.JSONDecodeError:
        return []
    rows = []
    for product in data.values():
        if not isinstance(product, dict):
            continue
        name = product.get("name") or ""
        price = None
        current = (product.get("price") or {}).get("current") or {}
        if current.get("amount"):
            try:
                price = float(current["amount"])
            except ValueError:
                price = None
        size = (product.get("size") or {}).get("value")
        rows.append({
            "name": name,
            "brand": product.get("brand"),
            "available": bool(product.get("available")),
            "in_catalog": product.get("isInCurrentCatalog"),
            "price": price,
            "size": size,
        })
    return rows


def shell_eggs(rows: list[dict]) -> list[dict]:
    return [r for r in rows if is_shell_egg(r["name"])]


def summarise_retailer(name: str, rows: list[dict]) -> dict:
    eggs = shell_eggs(rows)
    available = [r for r in eggs if r["available"]]
    priced = [r for r in available if r["price"] is not None]
    six = [r for r in priced if r.get("size") and re.search(r"\b6\b", r["size"])]
    twelve = [r for r in priced if r.get("size") and re.search(r"\b12\b", r["size"])]
    cheapest = min(priced, key=lambda r: r["price"]) if priced else None
    cheapest6 = min(six, key=lambda r: r["price"]) if six else None
    cheapest12 = min(twelve, key=lambda r: r["price"]) if twelve else None
    ratio = None if not eggs else round(len(available) / len(eggs), 2)
    return {
        "retailer": name,
        "listed": len(eggs),
        "available": len(available),
        "unavailable": len(eggs) - len(available),
        "available_ratio": ratio,
        "cheapest": None if not cheapest else {
            "name": cheapest["name"],
            "price": cheapest["price"],
            "size": cheapest["size"],
        },
        "cheapest_6": None if not cheapest6 else {"name": cheapest6["name"], "price": cheapest6["price"]},
        "cheapest_12": None if not cheapest12 else {"name": cheapest12["name"], "price": cheapest12["price"]},
    }


def ocado_like(retailer: str, search_url: str) -> dict:
    html = fetch(search_url)
    rows = parse_product_entities(html)
    summary = summarise_retailer(retailer, rows)
    summary["url"] = search_url
    if not rows:
        summary["error"] = "page returned no product list"
    return summary


def trolley_retailers(product_url: str) -> dict:
    html = fetch(product_url)
    title_m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""
    title = title.split(" - Compare")[0]
    found = re.findall(
        r'svg_([a-z0-9-]+)-logo[\s\S]{0,220}?&pound;([0-9]+(?:\.[0-9]+)?)',
        html,
    )
    retailers = [{"name": name.replace("-", " ").title(), "price": float(price)} for name, price in found]
    # de-dupe, keep cheapest
    best = {}
    for row in retailers:
        prev = best.get(row["name"])
        if prev is None or row["price"] < prev:
            best[row["name"]] = row["price"]
    retailers = [{"name": n, "price": p} for n, p in sorted(best.items(), key=lambda kv: kv[1])]
    return {"product": title, "url": product_url, "retailers": retailers}


def summarise_named(retailer: str, rows: list[dict], keep) -> dict:
    kept = [r for r in rows if keep(r["name"])]
    available = [r for r in kept if r["available"]]
    priced = [r for r in available if r["price"] is not None]
    cheapest = min(priced, key=lambda r: r["price"]) if priced else None
    ratio = None if not kept else round(len(available) / len(kept), 2)
    return {
        "retailer": retailer,
        "listed": len(kept),
        "available": len(available),
        "unavailable": len(kept) - len(available),
        "available_ratio": ratio,
        "cheapest": None if not cheapest else {
            "name": cheapest["name"],
            "price": cheapest["price"],
            "size": cheapest["size"],
        },
    }


def ocado_search(label: str, query: str, keep) -> dict:
    url = f"https://www.ocado.com/search?q={quote(query)}"
    html = fetch(url)
    summary = summarise_named("Ocado", parse_product_entities(html), keep)
    summary["item"] = label
    summary["url"] = url
    return summary


STAPLES = [
    ("Eggs", "https://www.trolley.co.uk/product/st-ewe-rich-yolk-free-range-eggs/GJS701"),
    ("Eggs", "https://www.trolley.co.uk/product/freshlay-family-run-farms-golden-yolks-free-range-eggs/WHD382"),
    ("Milk", "https://www.trolley.co.uk/product/grahams-gold-jersey-milk/SOY634"),
    ("Bread", "https://www.trolley.co.uk/product/warburtons-toastie-thick-sliced-soft-white-bread/ZUK263"),
]


def check() -> dict:
    errors = []
    retailers = []
    try:
        row = ocado_like("Morrisons", "https://groceries.morrisons.com/search?q=eggs")
        retailers.append(row)
    except Exception as exc:
        errors.append(f"Morrisons: {exc}")

    ocado = None
    try:
        ocado = ocado_like("Ocado", "https://www.ocado.com/search?q=eggs")
        if ocado.get("error") or not ocado.get("listed"):
            ocado["blocked"] = True
            ocado["error"] = ocado.get("error") or "Ocado blocked the check. Not treated as out of stock."
        retailers.append(ocado)
    except Exception as exc:
        errors.append(f"Ocado: {exc}")
        retailers.append({
            "retailer": "Ocado",
            "blocked": True,
            "error": f"Ocado blocked the check ({exc}). Not treated as out of stock.",
            "listed": 0,
            "available": 0,
        })

    staples = []
    for label, url in STAPLES:
        try:
            row = trolley_retailers(url)
            row["item"] = label
            staples.append(row)
        except Exception as exc:
            errors.append(f"Trolley {label}: {exc}")

    # Stress only from shops that answered. A block is not a shortage.
    live = [r for r in retailers if r.get("available_ratio") is not None and not r.get("blocked")]
    if live:
        avail_stress = round((1 - sum(r["available_ratio"] for r in live) / len(live)) * 100)
    else:
        avail_stress = None
    # If a staple that usually has 3+ retailers comes back with none, that is a real gap.
    thin = [s for s in staples if len(s.get("retailers") or []) == 0]
    if thin and avail_stress is not None:
        avail_stress = min(100, avail_stress + 25 * len(thin))
    elif thin and avail_stress is None:
        avail_stress = min(100, 25 * len(thin))

    return {
        "item": "eggs",
        "retailers": retailers,
        "staples": staples,
        "trolley": next((s for s in staples if s.get("item") == "Eggs" and len(s.get("retailers") or []) >= 2), None),
        "availability_stress": avail_stress,
        "errors": errors,
        "note": (
            "Online catalogues, not the shop on your street. "
            "Trolley shows which supermarkets currently publish a price for the same product. "
            "Morrisons reports an available flag. "
            "Tesco, Asda and Sainsbury's block direct checks. "
            "If Ocado blocks the read, that is a block, not an empty shelf."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(check(), indent=2))
