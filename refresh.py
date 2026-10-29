#!/usr/bin/env python3
"""Pull public UK food and fuel signals and write data/latest.json.

This is a watch index, not a forecast of empty shelves.
Missing feeds stay missing. Nothing is filled in.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from http_fetch import fetch_bytes

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "latest.json"
UA = "Mozilla/5.0 (compatible; UKSupplyWatch/1.0; local household monitor)"

FUEL_CSV = (
    "https://assets.publishing.service.gov.uk/media/"
    "6adf6252d798ae11e9bc5d07/CSV__2018_-__.csv"
)

REGIONS = [
    {"id": "almeria", "name": "Almería", "role": "Spanish salad and tomatoes", "lat": 36.84, "lon": -2.46},
    {"id": "agadir", "name": "Agadir", "role": "Moroccan tomatoes", "lat": 30.42, "lon": -9.60},
    {"id": "westland", "name": "Westland", "role": "Dutch glasshouses", "lat": 52.02, "lon": 4.22},
    {"id": "anglia", "name": "East Anglia", "role": "UK wheat and field veg", "lat": 52.40, "lon": 0.75},
]

TICKERS = {
    "brent": "BZ=F",
    "ttf": "TTF=F",
    "wheat": "ZW=F",
}


HOSTS = {
    "assets.publishing.service.gov.uk",
    "query1.finance.yahoo.com",
    "archive-api.open-meteo.com",
}


def fetch(url: str, timeout: int = 40) -> bytes:
    return fetch_bytes(url, allow_hosts=HOSTS, timeout=timeout, max_bytes=8_000_000, headers={"User-Agent": UA, "Accept": "*/*"})


def yahoo(symbol: str, range_: str = "2y") -> dict:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?interval=1d&range={range_}"
    )
    payload = json.loads(fetch(url))
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    ts = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    series = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        series.append({"date": datetime.fromtimestamp(t, timezone.utc).date().isoformat(), "close": round(float(c), 2)})
    return {
        "symbol": symbol,
        "name": meta.get("shortName") or meta.get("longName") or symbol,
        "currency": meta.get("currency"),
        "price": series[-1]["close"] if series else None,
        "as_of": series[-1]["date"] if series else None,
        "series": series,
    }


def pct_change(series: list[dict], days: int) -> float | None:
    if len(series) < 2:
        return None
    end = series[-1]["close"]
    cutoff = date.fromisoformat(series[-1]["date"]) - timedelta(days=days)
    prior = None
    for row in series:
        if date.fromisoformat(row["date"]) <= cutoff:
            prior = row["close"]
    if prior is None:
        prior = series[0]["close"]
    if not prior:
        return None
    return round((end - prior) / prior * 100, 1)


def percentile(value: float, history: list[float]) -> float | None:
    if not history:
        return None
    below = sum(1 for x in history if x <= value)
    return round(100 * below / len(history), 1)


def band(score: float) -> str:
    if score < 25:
        return "Calm"
    if score < 45:
        return "Watch"
    if score < 65:
        return "Elevated"
    if score < 85:
        return "Stress"
    return "Severe"


def score_from_percentile(p: float | None, change: float | None = None, change_kick: float = 8) -> float | None:
    if p is None:
        return None
    # Map a historical percentile into a 0-100 stress score.
    # Median is calm-watch. The top decile is stress.
    if p < 50:
        base = p * 0.4
    elif p < 80:
        base = 20 + (p - 50) * 1.1
    else:
        base = 53 + (p - 80) * 2.0
    if change is not None and change > change_kick:
        base += min(12, (change - change_kick) * 0.6)
    elif change is not None and change < -change_kick:
        base -= min(8, (-change - change_kick) * 0.3)
    return max(0, min(100, round(base)))


def parse_fuel_csv(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) < 3:
            continue
        try:
            d = datetime.strptime(parts[0].strip(), "%d/%m/%Y").date()
            petrol = float(parts[1])
            diesel = float(parts[2])
        except ValueError:
            continue
        out.append({"date": d.isoformat(), "petrol": petrol, "diesel": diesel})
    return out


def meteo(lat: float, lon: float, start: date, end: date) -> list[dict]:
    url = (
        "https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={lat}&longitude={lon}"
        f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
        "&daily=precipitation_sum,temperature_2m_mean&timezone=UTC"
    )
    payload = json.loads(fetch(url, timeout=60))
    daily = payload["daily"]
    rows = []
    for d, p, t in zip(daily["time"], daily["precipitation_sum"], daily["temperature_2m_mean"]):
        rows.append({"date": d, "rain": p, "temp": t})
    return rows


def window_sum(rows: list[dict], start: date, end: date, key: str) -> float | None:
    vals = []
    for row in rows:
        d = date.fromisoformat(row["date"])
        if start <= d <= end and row[key] is not None:
            vals.append(row[key])
    if not vals:
        return None
    return sum(vals)


def region_anomaly(region: dict, today: date) -> dict:
    end = today - timedelta(days=2)  # archive lags a day or two
    start_hist = date(end.year - 10, 1, 1)
    rows = meteo(region["lat"], region["lon"], start_hist, end)
    win_end = end
    win_start = end - timedelta(days=44)
    this_rain = window_sum(rows, win_start, win_end, "rain")
    this_temp_rows = [
        r["temp"] for r in rows
        if r["temp"] is not None and win_start <= date.fromisoformat(r["date"]) <= win_end
    ]
    this_temp = statistics.mean(this_temp_rows) if this_temp_rows else None
    hist_rain = []
    hist_temp = []
    for year in range(end.year - 10, end.year):
        try:
            hs = date(year, win_start.month, win_start.day)
            he = date(year, win_end.month, win_end.day)
        except ValueError:
            continue
        if he < hs:
            he = date(year + 1, win_end.month, win_end.day)
        rain = window_sum(rows, hs, he, "rain")
        temps = [
            r["temp"] for r in rows
            if r["temp"] is not None and hs <= date.fromisoformat(r["date"]) <= he
        ]
        if rain is not None:
            hist_rain.append(rain)
        if temps:
            hist_temp.append(statistics.mean(temps))
    rain_pct = percentile(this_rain, hist_rain) if this_rain is not None else None
    # Dry is the stress. A low rain percentile is bad for rainfed and for reservoirs.
    dryness = None if rain_pct is None else round(100 - rain_pct, 1)
    temp_pct = percentile(this_temp, hist_temp) if this_temp is not None else None
    normal_rain = statistics.median(hist_rain) if hist_rain else None
    rain_vs = None
    if this_rain is not None and normal_rain:
        rain_vs = round((this_rain - normal_rain) / normal_rain * 100, 0)
    # Stress if unusually dry or unusually hot. Glasshouses care more about energy than rain,
    # so Westland dryness is a weaker signal. Still report it.
    # Dryness is the shortage signal. Heat only adds when it is in the top of the local record.
    # A warm October with normal rain is not a failed harvest.
    stress = None
    if dryness is not None and temp_pct is not None and temp_pct >= 85:
        stress = round(dryness * 0.75 + temp_pct * 0.25)
    elif dryness is not None:
        stress = round(dryness)
    elif temp_pct is not None and temp_pct >= 85:
        stress = round(temp_pct * 0.4)
    return {
        "id": region["id"],
        "name": region["name"],
        "role": region["role"],
        "window": f"{win_start.isoformat()} to {win_end.isoformat()}",
        "rain_mm": None if this_rain is None else round(this_rain, 1),
        "normal_rain_mm": None if normal_rain is None else round(normal_rain, 1),
        "rain_vs_normal_pct": rain_vs,
        "dryness_percentile": dryness,
        "temp_c": None if this_temp is None else round(this_temp, 1),
        "temp_percentile": temp_pct,
        "stress": stress,
    }


def mean(vals: list[float | None]) -> float | None:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean))


def main() -> None:
    now = datetime.now(timezone.utc)
    today = now.date()
    errors = []

    fuel_rows = []
    try:
        fuel_rows = parse_fuel_csv(fetch(FUEL_CSV))
    except Exception as exc:
        errors.append(f"UK pump prices: {exc}")

    markets = {}
    for key, symbol in TICKERS.items():
        try:
            markets[key] = yahoo(symbol, "2y")
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            markets[key] = None

    regions = []
    for region in REGIONS:
        try:
            regions.append(region_anomaly(region, today))
        except Exception as exc:
            errors.append(f"{region['id']}: {exc}")
            regions.append({
                "id": region["id"],
                "name": region["name"],
                "role": region["role"],
                "stress": None,
                "error": str(exc),
            })

    petrol_hist = [r["petrol"] for r in fuel_rows]
    diesel_hist = [r["diesel"] for r in fuel_rows]
    latest_fuel = fuel_rows[-1] if fuel_rows else None
    petrol_4w = None
    diesel_4w = None
    if len(fuel_rows) >= 5:
        petrol_4w = round((fuel_rows[-1]["petrol"] - fuel_rows[-5]["petrol"]) / fuel_rows[-5]["petrol"] * 100, 1)
        diesel_4w = round((fuel_rows[-1]["diesel"] - fuel_rows[-5]["diesel"]) / fuel_rows[-5]["diesel"] * 100, 1)

    petrol_p = percentile(latest_fuel["petrol"], petrol_hist) if latest_fuel else None
    diesel_p = percentile(latest_fuel["diesel"], diesel_hist) if latest_fuel else None
    petrol_score = score_from_percentile(petrol_p, petrol_4w, 2)
    diesel_score = score_from_percentile(diesel_p, diesel_4w, 2)

    def market_score(key: str, kick: float) -> tuple[float | None, float | None, float | None]:
        m = markets.get(key)
        if not m or not m["series"]:
            return None, None, None
        closes = [r["close"] for r in m["series"]]
        p = percentile(m["price"], closes)
        ch = pct_change(m["series"], 30)
        return score_from_percentile(p, ch, kick), p, ch

    brent_score, brent_p, brent_30 = market_score("brent", 8)
    ttf_score, ttf_p, ttf_30 = market_score("ttf", 10)
    wheat_score, wheat_p, wheat_30 = market_score("wheat", 8)

    fuel_parts = [p for p in (petrol_score, diesel_score, brent_score, ttf_score) if p is not None]
    fuel_index = round(sum(fuel_parts) / len(fuel_parts)) if fuel_parts else None

    import_regions = [r for r in regions if r["id"] in ("almeria", "agadir", "westland")]
    uk_region = next((r for r in regions if r["id"] == "anglia"), None)
    import_weather = mean([r.get("stress") for r in import_regions])
    uk_weather = uk_region.get("stress") if uk_region else None
    # Standing exposure of the fresh-food basket. From Defra 2023/2025 ratios.
    # Fruit ~14-16% home supply, vegetables ~53-56%, salad imports concentrated.
    # This does not move daily. It is labelled as structural.
    structural_fresh = 58

    food_weights = []
    harvest = None
    try:
        from harvest import read as read_harvest
        harvest = read_harvest()
    except Exception as exc:
        errors.append(f"harvest: {exc}")

    spare = None
    try:
        from energy_security import read as read_spare
        spare = read_spare()
        errors.extend(spare.get("notes") or [])
    except Exception as exc:
        errors.append(f"spare: {exc}")

    shelf = None
    try:
        from availability import check as shelf_check
        shelf = shelf_check()
        errors.extend(shelf.get("errors") or [])
    except Exception as exc:
        errors.append(f"shelf: {exc}")
    if import_weather is not None:
        food_weights.append((import_weather, 0.20, "import-belt weather"))
    if wheat_score is not None:
        food_weights.append((wheat_score, 0.15, "wheat price"))
    if uk_weather is not None:
        food_weights.append((uk_weather, 0.10, "UK field weather"))
    if harvest and harvest.get("score") is not None:
        food_weights.append((harvest["score"], 0.30, "this harvest"))
    if shelf and shelf.get("availability_stress") is not None:
        food_weights.append((shelf["availability_stress"], 0.10, "shelf"))
    food_weights.append((22, 0.08, "structural cereals"))
    food_weights.append((structural_fresh, 0.07, "structural fresh food"))
    weight_sum = sum(w for _, w, _ in food_weights)
    food_index = round(sum(v * w for v, w, _ in food_weights) / weight_sum) if food_weights else None

    def why_food() -> str:
        bits = []
        dry = [r for r in import_regions if (r.get("rain_vs_normal_pct") or 0) <= -25]
        wet = [r for r in import_regions if (r.get("rain_vs_normal_pct") or 0) >= 25]
        if dry:
            bits.append(f"{', '.join(r['name'] for r in dry)} is drier than its usual autumn")
        elif wet:
            bits.append("import belts are wetter than usual, which is not a drought signal")
        else:
            bits.append("import-belt rain is near normal")
        if wheat_30 is not None:
            direction = "up" if wheat_30 > 0 else "down"
            bits.append(f"wheat futures are {direction} {abs(wheat_30):.0f}% in 30 days")
        if harvest:
            bits.insert(0, f"this harvest is the loud food score: wheat yields {abs(harvest['wheat_vs_five_year_pct']):.0f}% below the five-year average after a dry spring and summer")
        bits.append("the overall does not cancel that. Shelves can still be stocked while the crop was short")
        return ". ".join(bits) + "."

    def why_fuel() -> str:
        bits = []
        if latest_fuel:
            bits.append(
                f"diesel is {latest_fuel['diesel']:.0f}p and petrol {latest_fuel['petrol']:.0f}p, week of {latest_fuel['date']}"
            )
        if diesel_p is not None:
            bits.append(f"diesel is at the {diesel_p:.0f}th percentile of pump prices since 2018")
        if brent_30 is not None and markets.get("brent"):
            bits.append(f"Brent is ${markets['brent']['price']:.0f}, {brent_30:+.0f}% in 30 days")
        if ttf_30 is not None and markets.get("ttf"):
            bits.append(f"Dutch gas is {markets['ttf']['price']:.0f}, {ttf_30:+.0f}% in 30 days")
        return ". ".join(bits) + "." if bits else "Fuel feeds did not return a readable series."

    def why_spare() -> str:
        if not spare or spare.get("score") is None:
            return "Stock and capacity feeds did not return a reading."
        oil = spare.get("oil") or {}
        power = (spare.get("power") or {}).get("tight") or {}
        bits = []
        if oil.get("diesel_days") is not None:
            bits.append(f"white diesel in the depot covers about {oil['diesel_days']:.0f} days of the latest month's deliveries")
        if power.get("spare_mw") is not None:
            bits.append(f"the tightest power day still has {power['spare_mw']/1000:.1f} GW spare")
        bits.append("these scores are not the pump price, and a calm spare reading does not make diesel cheap")
        return ". ".join(bits) + "."

    egg_note = "Online catalogues did not return an egg list."
    egg_score = None
    egg_stance = "No live feed"
    if shelf and shelf.get("availability_stress") is not None:
        egg_score = shelf["availability_stress"]
        egg_stance = "On the shelf"
        bits = []
        for row in shelf.get("retailers") or []:
            if row.get("blocked"):
                bits.append(f"{row['retailer']} blocked the check, not counted as empty")
            elif row.get("listed"):
                bits.append(f"{row['retailer']} {row['available']}/{row['listed']} in stock")
                cheap = row.get("cheapest_6") or row.get("cheapest")
                if cheap:
                    bits[-1] += f", cheapest £{cheap['price']:.2f}"
        shops = []
        for staple in shelf.get("staples") or []:
            if staple.get("item") != "Eggs":
                continue
            names = [r["name"] for r in staple.get("retailers") or []]
            if names:
                shops.append(", ".join(names))
        if shops:
            bits.append("Trolley shows egg prices at " + shops[0])
        egg_note = ". ".join(bits) + "." if bits else egg_note

    categories = [
        {
            "id": "salad",
            "name": "Salad and tomatoes",
            "stance": "The live one",
            "score": import_weather,
            "note": "Spain, Morocco and Dutch glasshouses. Short shelf life, Dover, almost no stock.",
        },
        {
            "id": "fruit",
            "name": "Fruit",
            "stance": "Standing",
            "score": 62,
            "note": "About 14% home-produced in 2025. No daily production feed. Score is structural, not a live shock.",
        },
        {
            "id": "cereals",
            "name": "Bread and cereals",
            "stance": "This harvest",
            "score": harvest["score"] if harvest else wheat_score,
            "note": harvest["detail"] if harvest else "Harvest page did not load. Wheat price is the fallback.",
        },
        {
            "id": "eggs",
            "name": "Eggs",
            "stance": egg_stance,
            "score": egg_score,
            "note": egg_note,
        },
        {
            "id": "meat",
            "name": "Meat cuts",
            "stance": "Standing",
            "score": 40,
            "note": "Tonnes are mostly domestic. Breast meat is the imported cut. No daily cut-level feed.",
        },
    ]

    def part(name, score, detail):
        return {
            "name": name,
            "score": score,
            "band": None if score is None else band(score),
            "detail": detail,
        }

    pump_score = None
    pump_bits = [s for s in (petrol_score, diesel_score) if s is not None]
    if pump_bits:
        pump_score = round(sum(pump_bits) / len(pump_bits))
    wholesale_bits = [s for s in (brent_score, ttf_score) if s is not None]
    wholesale_score = round(sum(wholesale_bits) / len(wholesale_bits)) if wholesale_bits else None
    shelf_score = shelf.get("availability_stress") if shelf else None
    parts = [
        part("This harvest", harvest["score"] if harvest else None, harvest["detail"] if harvest else "Harvest reading missing."),
        part("Usual cereal supply", 22, "Last published cereal production-to-supply ratio was 93% in 2023. That is the normal picture, not this crop."),
        part("Usual fresh food", structural_fresh, "Fruit is about 14% home-produced. Winter salad depends on Spain, Morocco and Dutch glasshouses."),
        part("Shelf", shelf_score, "Eggs, milk and bread priced on public shop pages. Zero means the listed lines were buyable, not that every local shelf is full."),
        part("Import weather", import_weather, "Last 45 days of rain in Almería, Agadir and Westland versus the same dates in the previous ten years."),
        part("Wheat price", wheat_score, "Chicago wheat futures versus their own two-year range. A falling price can sit next to a short crop."),
        part("Pump prices", pump_score, why_fuel()),
        part("Oil and gas prices", wholesale_score, "Brent and Dutch gas, each versus their own two-year range, then averaged. Price is not stock."),
        part("Emergency oil stocks", (spare.get("oil") or {}).get("emergency_score") if spare else None, (spare.get("oil") or {}).get("detail") if spare else "Oil stock table did not load."),
        part("Fuel in the depot", (spare.get("oil") or {}).get("product_score") if spare else None, "Days of petrol, diesel and jet implied by the latest month of deliveries. This is working stock, not the IEA reserve."),
        part("Gas in store", (spare.get("gas") or {}).get("storage_score") if spare else None, (spare.get("gas") or {}).get("detail") if spare else "Gas storage page did not load."),
        part("Gas day balance", (spare.get("gas") or {}).get("day_score") if spare else None, "Today's demand versus the level that would trigger a National Gas margins notice."),
        part("Power spare", (spare.get("power") or {}).get("score") if spare else None, (spare.get("power") or {}).get("detail") if spare else "Power margin did not load."),
    ]

    payload = {
        "generated_at": now.isoformat(),
        "as_of": today.isoformat(),
        "disclaimer": (
            "A watch index for UK type shortages and fuel stress. "
            "Individual scores come first. The overall is a blend and does not cancel a bad harvest. "
            "It does not see supermarket shelves, and it is not a prediction that a shop will be empty."
        ),
        "food": {
            "score": food_index,
            "band": None if food_index is None else band(food_index),
            "why": why_food(),
            "parts": [{"name": n, "score": v, "weight": w} for v, w, n in food_weights],
        },
        "fuel": {
            "score": fuel_index,
            "band": None if fuel_index is None else band(fuel_index),
            "why": why_fuel() + " Stocks and spare capacity are scored separately and do not cancel this.",
        },
        "spare": {
            "score": None if not spare else spare.get("score"),
            "band": None if not spare else spare.get("band"),
            "why": why_spare(),
        },
        "pumps": {
            "latest": latest_fuel,
            "petrol_percentile": petrol_p,
            "diesel_percentile": diesel_p,
            "petrol_4w_pct": petrol_4w,
            "diesel_4w_pct": diesel_4w,
            "petrol_score": petrol_score,
            "diesel_score": diesel_score,
            "source": FUEL_CSV,
            "series": fuel_rows[-104:],
        },
        "markets": {
            key: None if not markets.get(key) else {
                "name": markets[key]["name"],
                "symbol": markets[key]["symbol"],
                "currency": markets[key]["currency"],
                "price": markets[key]["price"],
                "as_of": markets[key]["as_of"],
                "change_30d_pct": pct_change(markets[key]["series"], 30),
                "percentile_2y": percentile(markets[key]["price"], [r["close"] for r in markets[key]["series"]]),
                "series": markets[key]["series"][-120:],
            }
            for key in TICKERS
        },
        "regions": regions,
        "parts": parts,
        "harvest": harvest,
        "spare_detail": spare,
        "categories": categories,
        "shelf": shelf,
        "errors": errors,
        "method": [
            "Pump prices are the DESNZ weekly series from 2018. Stress is their percentile in that history, plus a kick if the last four weeks rose.",
            "Brent, Dutch TTF gas and Chicago wheat are daily futures from Yahoo, scored on their own two-year percentile and 30-day change.",
            "Weather stress is the last 45 days of rain and temperature versus the same calendar window in the previous ten years, from Open-Meteo.",
            "This harvest uses the AHDB final 2026 survey against the five-year yield average, not the change versus last year. Defra's England tonnage is shown beside it and is not allowed to cancel the yield gap.",
            "Fruit and meat standing scores do not move daily. They are the structural exposure from the UK Food Security Report, not a live shock.",
            "Eggs, milk, bread and chicken are read from public Ocado and Morrisons search pages, plus a Trolley price comparison for Tesco, Asda and Sainsbury's. That is an online catalogue, not your local shelf. A score of 0 means every listed shell egg was marked available.",
            "Emergency oil stocks are DESNZ Energy Trends table 3.11. The score stays in Watch while the last quarterly note says the IEA 90-day test is met, even if the tonne total is low for the decade.",
            "Fuel in the depot is table 3.11 stocks divided by table 3.13 deliveries in the same month. It is not the emergency reserve.",
            "Gas in store and the gas-day balance come from National Gas. Medium-range fill is the storage score. Long-range is shown and not treated as a buffer when it is published at zero.",
            "Power spare is the smallest NESO surplus over the next fortnight, set against that day's demand forecast from Elexon. It is spare generation, not a map of weak points.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"wrote {OUT}")
    print(f"food {food_index} {payload['food']['band']}")
    print(f"fuel {fuel_index} {payload['fuel']['band']}")
    if errors:
        print("errors:")
        for err in errors:
            print(" -", err)


if __name__ == "__main__":
    main()
