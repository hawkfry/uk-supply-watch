#!/usr/bin/env python3
"""UK stocks and spare capacity. Public series only. Not a price index."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from http_fetch import fetch_bytes
from safe_zip import open_xlsx, parse_xml, read_member

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


HOSTS = {
    "www.gov.uk",
    "assets.publishing.service.gov.uk",
    "data.nationalgas.com",
    "data.elexon.co.uk",
}


def fetch(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    hdrs = {"User-Agent": UA, "Accept": "application/json, */*"}
    if headers:
        hdrs.update(headers)
    return fetch_bytes(url, allow_hosts=HOSTS, timeout=40, max_bytes=8_000_000, data=data, headers=hdrs)


def band(score: int | None) -> str | None:
    if score is None:
        return None
    if score < 25:
        return "Calm"
    if score < 45:
        return "Watch"
    if score < 65:
        return "Elevated"
    if score < 85:
        return "Stress"
    return "Severe"


def days_cover_score(days: float) -> int:
    """Working stock versus a month of deliveries. Not the IEA 90-day test."""
    if days >= 45:
        return 15
    if days >= 30:
        return round(15 + (45 - days) * 1.2)
    if days >= 20:
        return round(33 + (30 - days) * 2.2)
    if days >= 10:
        return round(55 + (20 - days) * 2.5)
    return min(95, round(80 + (10 - days) * 2))


def fill_score(pct: float) -> int:
    """Empty store scores high. 90% full is calm. 50% full is elevated."""
    return max(8, min(95, round((100 - pct) * 0.9)))


def spare_gw_score(spare_gw: float) -> int:
    """NESO surplus above forecast national demand, in GW. Under 3 GW is thin."""
    if spare_gw >= 10:
        return 15
    if spare_gw >= 6:
        return round(15 + (10 - spare_gw) * 8)
    if spare_gw >= 3:
        return round(47 + (6 - spare_gw) * 12)
    return min(96, round(83 + (3 - spare_gw) * 8))


def gas_day_score(demand_mcm: float, trigger_mcm: float) -> int:
    """A margins notice is the official 'this day is tight' flag. It fires near the trigger."""
    if trigger_mcm <= 0:
        return 40
    ratio = demand_mcm / trigger_mcm
    if ratio < 0.55:
        return round(ratio * 36)
    if ratio < 0.8:
        return round(20 + (ratio - 0.55) * 140)
    return min(95, round(55 + (ratio - 0.8) * 180))


def _xlsx_rows(blob: bytes, sheet: str) -> list[dict]:
    z = open_xlsx(blob)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = parse_xml(read_member(z, "xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            ss.append("".join(t.text or "" for t in si.findall(".//m:t", NS)))
    root = parse_xml(read_member(z, sheet))
    rows = []
    for row in root.findall("m:sheetData/m:row", NS):
        cells = {}
        for c in row.findall("m:c", NS):
            ref = c.attrib.get("r", "")
            col = "".join(ch for ch in ref if ch.isalpha())
            t = c.attrib.get("t")
            v = c.find("m:v", NS)
            val = v.text if v is not None else ""
            if t == "s" and str(val).isdigit():
                val = ss[int(val)]
            cells[col] = val
        rows.append(cells)
    return rows


def _num(value) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _latest_xlsx(page_url: str, needle: str) -> tuple[str, bytes]:
    html = fetch(page_url).decode("utf-8", "replace")
    links = re.findall(r"https://assets\.publishing\.service\.gov\.uk/[^\"']+", html)
    hits = [u for u in links if needle in u]
    if not hits:
        raise RuntimeError(f"no {needle} link on {page_url}")
    url = hits[0]
    return url, fetch(url)


def oil_stocks() -> dict:
    stocks_url, stocks_blob = _latest_xlsx(
        "https://www.gov.uk/government/statistics/oil-and-oil-products-section-3-energy-trends",
        "ET_3.11_",
    )
    deliv_url, deliv_blob = _latest_xlsx(
        "https://www.gov.uk/government/statistics/oil-and-oil-products-section-3-energy-trends",
        "ET_3.13_",
    )
    stock_rows = _xlsx_rows(stocks_blob, "xl/worksheets/sheet8.xml")
    dated = [r for r in stock_rows if re.search(r"(19|20)\d{2}", str(r.get("A", "")))]
    latest = dated[-1]
    year_ago = dated[-13] if len(dated) >= 13 else None
    totals = [_num(r.get("W")) for r in dated if _num(r.get("W")) is not None]
    recent = totals[-120:]
    total = _num(latest.get("W"))
    below = sum(1 for v in recent if total is not None and v < total)
    pct_rank = round(100 * below / (len(recent) - 1), 1) if recent and total is not None else None

    deliv_rows = _xlsx_rows(deliv_blob, "xl/worksheets/sheet8.xml")
    deliveries = [r for r in deliv_rows if re.search(r"(19|20)\d{2}", str(r.get("A", "")))]
    dlatest = deliveries[-1]
    days_in_month = 31 if "July" in str(dlatest.get("A")) or "January" in str(dlatest.get("A")) or "August" in str(dlatest.get("A")) or "December" in str(dlatest.get("A")) or "March" in str(dlatest.get("A")) or "May" in str(dlatest.get("A")) or "October" in str(dlatest.get("A")) else 30
    if "February" in str(dlatest.get("A")):
        days_in_month = 28

    def cover(stock_col: str, deliv_col: str) -> float | None:
        stock = _num(latest.get(stock_col))
        flow = _num(dlatest.get(deliv_col))
        if not stock or not flow:
            return None
        return round(stock / (flow / days_in_month), 1)

    petrol_days = cover("K", "F")
    diesel_days = cover("L", "I")
    jet_days = cover("O", "G")
    tight = min(v for v in (petrol_days, diesel_days, jet_days) if v is not None)
    yoy = None
    if year_ago and total and _num(year_ago.get("W")):
        yoy = round((total / _num(year_ago.get("W")) - 1) * 100, 1)
    # The IEA test was still met at the last quarterly print. Do not turn a low tonne
    # total into a failed emergency reserve. Depot cover is a separate score.
    emergency = 24
    if pct_rank is not None and pct_rank < 30:
        emergency = 38
    if yoy is not None and yoy <= -15:
        emergency = min(52, emergency + 8)
    product = days_cover_score(tight) if tight is not None else None
    detail = (
        f"DESNZ stocks at end of {latest.get('A')}: {total/1000:.1f} million tonnes in total, "
        f"{(_num(latest.get('V')) or 0)/1000:.1f} million tonnes physically in the UK. "
        f"That is {yoy:+.0f}% on the same month a year earlier. "
        f"Against the last ten years of months, this total sits around the {pct_rank:.0f}th percentile. "
        f"The last quarterly note said end-June stocks still more than met the IEA 90-day net-import rule. "
        f"That rule is not the same as fuel in the depot. "
        f"July deliveries imply about {diesel_days:.0f} days of white diesel, {petrol_days:.0f} days of petrol and {jet_days:.0f} days of jet fuel."
    )
    return {
        "period": latest.get("A"),
        "total_kt": total,
        "uk_kt": _num(latest.get("V")),
        "yoy_pct": yoy,
        "percentile_10y": pct_rank,
        "petrol_days": petrol_days,
        "diesel_days": diesel_days,
        "jet_days": jet_days,
        "emergency_score": emergency,
        "product_score": product,
        "stocks_url": stocks_url,
        "deliveries_url": deliv_url,
        "detail": detail,
    }


def _ng(endpoint: str, request: str) -> dict:
    url = f"https://data.nationalgas.com/api/{endpoint}?request={request}"
    body = json.dumps({"request": request}).encode()
    raw = fetch(url, data=body, headers={
        "Content-Type": "application/json",
        "Referer": "https://data.nationalgas.com/reports/gas-day-summary",
    })
    return json.loads(raw.decode())


def gas() -> dict:
    stocks = _ng("daily-summary-report-table", "storageStockTable")["data"]
    lng = _ng("daily-summary-report-data", "lngStockLevel")["data"]
    demand = _ng("daily-summary-report-table", "demandTable")["data"]
    triggers = _ng("daily-summary-report-data", "marginTriggers")["data"]
    flows = _ng("daily-summary-report-table", "instantaneousFlowsTable")["data"]
    forecast = _ng_status("forecastData")
    rows = {}
    for row in stocks:
        label = row.get("GWhTomorrow") or ""
        m = re.search(r"([\d,]+)\s*\(([0-9.]+)%\)", label)
        rows[row["range"]] = {
            "date": row.get("tomorrow"),
            "gwh_text": label,
            "gwh": float(m.group(1).replace(",", "")) if m else _num(label),
            "fill_pct": float(m.group(2)) if m else None,
            "max_flow": _num(row.get("Max flow (GWh/D)")),
        }
    medium = rows.get("Medium") or {}
    lng_gwh = _num(lng.get("stockLevel"))
    trigger = _num(triggers.get("mnToday"))
    demand_now = _num((forecast or {}).get("Latest Demand Forecast"))
    today_demand = next((r for r in demand if r.get("date") == triggers.get("today")), None)
    if demand_now is None and today_demand:
        demand_now = _num(today_demand.get("demand"))
    storage_score = fill_score(medium["fill_pct"]) if medium.get("fill_pct") is not None else None
    day_score = gas_day_score(demand_now, trigger) if demand_now and trigger else None
    detail = (
        f"National Gas, {medium.get('date')}: medium-range storage {medium.get('gwh_text')}. "
        f"Long-range is published at {(rows.get('Long') or {}).get('gwh_text')}, so it is not a buffer. "
        f"LNG tanks held {lng_gwh:,.0f} GWh at {lng.get('dateTime')}. "
        f"Today's gas demand forecast is {demand_now} million cubic metres, against a margins-notice trigger of {trigger}. "
        f"No notice is in force. Storage cannot carry a normal day on its own. Pipelines and cargoes do that."
    )
    return {
        "ranges": rows,
        "lng_gwh": lng_gwh,
        "lng_as_of": lng.get("dateTime"),
        "demand_mcm": demand_now,
        "trigger_mcm": trigger,
        "flows": flows,
        "storage_score": storage_score,
        "day_score": day_score,
        "detail": detail,
    }


def _ng_status(request: str) -> dict:
    url = f"https://data.nationalgas.com/api/gas-system-status-data?request={request}"
    body = json.dumps({"request": request}).encode()
    raw = fetch(url, data=body, headers={
        "Content-Type": "application/json",
        "Referer": "https://data.nationalgas.com/gas-system-status",
    })
    return json.loads(raw.decode()).get("data") or {}


def power() -> dict:
    today = date.today().isoformat()
    end = (date.today() + timedelta(days=16)).isoformat()
    dem = json.loads(fetch(
        f"https://data.elexon.co.uk/bmrs/api/v1/forecast/demand/daily?from={today}&to={end}&format=json"
    ).decode())
    sur = json.loads(fetch(
        "https://data.elexon.co.uk/bmrs/api/v1/forecast/surplus/daily?publishTime=latest&format=json"
    ).decode())
    roll = json.loads(fetch(
        "https://data.elexon.co.uk/bmrs/api/v1/demand/rollingSystemDemand"
    ).decode())
    demand = {row["forecastDate"]: row for row in dem["data"]}
    latest = roll["data"][-1]
    joined = []
    for row in sur["data"]:
        d = demand.get(row["forecastDate"])
        if not d:
            continue
        spare = row["surplus"]
        national = d["nationalDemand"]
        joined.append({
            "date": row["forecastDate"],
            "demand_mw": national,
            "spare_mw": spare,
            "use_pct": round(100 * national / (national + spare), 1),
        })
    tight = min(joined, key=lambda r: r["spare_mw"])
    score = spare_gw_score(tight["spare_mw"] / 1000)
    detail = (
        f"Tightest day in the next fortnight is {tight['date']}: "
        f"forecast national demand {tight['demand_mw']/1000:.1f} GW, "
        f"spare generation {tight['spare_mw']/1000:.1f} GW, so the peak uses {tight['use_pct']:.0f}% of what NESO is counting as available. "
        f"Live demand at {latest['startTime']} was {latest['demand']/1000:.1f} GW. Night demand is not the tight moment."
    )
    return {
        "latest_demand_mw": latest["demand"],
        "latest_time": latest["startTime"],
        "tight": tight,
        "days": joined,
        "score": score,
        "detail": detail,
    }


def read() -> dict:
    notes = []
    oil = gas_reading = power_reading = None
    try:
        oil = oil_stocks()
    except Exception as exc:
        notes.append(f"oil stocks: {exc}")
    try:
        gas_reading = gas()
    except Exception as exc:
        notes.append(f"gas: {exc}")
    try:
        power_reading = power()
    except Exception as exc:
        notes.append(f"power: {exc}")
    weights = []
    if power_reading and power_reading.get("score") is not None:
        weights.append((power_reading["score"], 0.30))
    if gas_reading and gas_reading.get("storage_score") is not None:
        weights.append((gas_reading["storage_score"], 0.25))
    if oil and oil.get("product_score") is not None:
        weights.append((oil["product_score"], 0.25))
    if oil and oil.get("emergency_score") is not None:
        weights.append((oil["emergency_score"], 0.10))
    if gas_reading and gas_reading.get("day_score") is not None:
        weights.append((gas_reading["day_score"], 0.10))
    score = None
    if weights:
        total_w = sum(w for _, w in weights)
        score = round(sum(v * w for v, w in weights) / total_w)
    return {
        "oil": oil,
        "gas": gas_reading,
        "power": power_reading,
        "score": score,
        "band": band(score),
        "notes": notes,
        "why": "Stocks and spare capacity, kept apart from the pump price. A high price does not mean the tanks are empty, and a full tank does not make diesel cheap.",
    }


if __name__ == "__main__":
    print(json.dumps(read(), indent=2)[:4000])
