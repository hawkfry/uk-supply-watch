#!/usr/bin/env python3
"""World grain balance from the latest USDA WASDE XML.

This is a stock reading, not a price and not a UK harvest score.
A comfortable world stock must not cancel a short UK crop.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

PAGE_URL = (
    "https://www.usda.gov/about-usda/general-information/staff-offices/"
    "office-chief-economist/commodity-markets/wasde-report"
)
XML_LINK = re.compile(
    r"https://www\.usda\.gov/oce/commodity/wasde/wasde(\d{2})(\d{2})\.xml"
)
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def latest_xml_url(page_html: str) -> str:
    found = []
    for match in XML_LINK.finditer(page_html):
        month = int(match.group(1))
        year = 2000 + int(match.group(2))
        if 1 <= month <= 12:
            found.append((year, month, match.group(0)))
    if not found:
        raise ValueError("WASDE page has no XML link")
    found.sort()
    return found[-1][2]


def _norm(value: str | None) -> str:
    return " ".join((value or "").replace("\r", " ").split())


def _number(value: str | None) -> float | None:
    if not value or value == "filler":
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _cell(node) -> float | None:
    for cell in node.iter("Cell"):
        for key, value in cell.attrib.items():
            if key.startswith("cell_value"):
                parsed = _number(value)
                if parsed is not None:
                    return parsed
    return None


def _label(group, label_attr: str) -> str:
    direct = _norm(group.get(label_attr))
    if direct:
        return direct
    for node in group.iter():
        if node is group:
            continue
        nested = _norm(node.get(label_attr))
        if nested:
            return nested
    return ""


def _key(label: str) -> str:
    text = _norm(label).lower()
    if text.startswith("ending stock"):
        return "Ending Stocks"
    if text.startswith("total use"):
        return "Total Use"
    if text.startswith("domestic total"):
        return "Domestic Total"
    return _norm(label)


def _attrs(month, group_tag: str, label_attr: str) -> dict[str, float]:
    out = {}
    for group in month.iter(group_tag):
        label = _key(_label(group, label_attr))
        value = _cell(group)
        if label and value is not None and label not in out:
            out[label] = value
    return out


def _month_num(label: str | None) -> int:
    key = _norm(label).lower()[:3]
    return MONTHS.get(key, 0)


def _pair(months: list[tuple[int, dict[str, float]]], report_month: int) -> tuple[dict, dict | None]:
    # The continued page repeats the region name. The first filled block is the world total.
    first: dict[int, dict[str, float]] = {}
    for num, attrs in months:
        if attrs and num not in first:
            first[num] = attrs
    usable = sorted(first.items())
    if not usable:
        raise ValueError("WASDE table has no filled month")
    current = first.get(report_month)
    if current is None:
        report_month, current = usable[-1]
    prior = None
    for num, attrs in usable:
        if num < report_month:
            prior = attrs
    return current, prior


def _stocks_use(stocks: float | None, use: float | None) -> float | None:
    if stocks is None or not use:
        return None
    return round(100 * stocks / use, 1)


def _row(name: str, role: str, current: dict, prior: dict | None, use_key: str) -> dict:
    stocks = current.get("Ending Stocks")
    use = current.get(use_key)
    ratio = _stocks_use(stocks, use)
    prior_ratio = None
    if prior:
        prior_ratio = _stocks_use(prior.get("Ending Stocks"), prior.get(use_key))
    change = None
    if ratio is not None and prior_ratio is not None:
        change = round(ratio - prior_ratio, 1)
    return {
        "name": name,
        "role": role,
        "stocks_mt": stocks,
        "use_mt": use,
        "stocks_use_pct": ratio,
        "prior_stocks_use_pct": prior_ratio,
        "change_pts": change,
    }


def parse_wasde(xml_bytes: bytes) -> dict:
    text = xml_bytes.decode("utf-8-sig", errors="replace")
    root = ET.fromstring(text)
    report_month = 0
    report_label = ""
    for report in root.iter("Report"):
        label = report.get("Report_Month") or ""
        if label:
            report_label = label
            report_month = _month_num(label)
            break

    wheat_months = []
    for report in root.iter("Report"):
        if report.get("Name") != "sr08":
            continue
        for group in report.iter("m1_commodity_group"):
            if _norm(group.get("commodity1")) != "Wheat":
                continue
            for year in group.iter("m1_year_group"):
                if "Proj" not in (year.get("market_year1") or ""):
                    continue
                for month in year.iter("m1_month_group"):
                    attrs = _attrs(month, "m1_attribute_group", "attribute1")
                    if attrs.get("Ending Stocks") and attrs.get("Total Use"):
                        wheat_months.append((_month_num(month.get("forecast_month1")), attrs))

    corn_months = []
    for report in root.iter("Report"):
        if report.get("Name") != "sr23":
            continue
        for region in report.iter("m1_region_group"):
            if not _norm(region.get("region1")).startswith("World"):
                continue
            for month in region.iter("m1_month_group"):
                attrs = _attrs(month, "m1_attribute_group", "attribute1")
                if attrs.get("Ending Stocks") and attrs.get("Domestic Total"):
                    corn_months.append((_month_num(month.get("forecast_month1")), attrs))

    soy_months = []
    for report in root.iter("Report"):
        if report.get("Name") != "sr28":
            continue
        for region in report.iter("m1_region_group3"):
            if not _norm(region.get("region2")).startswith("World"):
                continue
            for month in region.iter("m1_month_group2"):
                attrs = _attrs(month, "m1_attribute_group3", "attribute2")
                if attrs.get("Ending Stocks") and attrs.get("Domestic Total"):
                    soy_months.append((_month_num(month.get("forecast_month2")), attrs))

    if not wheat_months:
        raise ValueError("WASDE XML has no world wheat balance")
    wheat_now, wheat_prior = _pair(wheat_months, report_month)
    corn_now, corn_prior = _pair(corn_months, report_month) if corn_months else ({}, None)
    soy_now, soy_prior = _pair(soy_months, report_month) if soy_months else ({}, None)
    released = date(2000, report_month, 1).strftime("%B") if report_month else report_label
    year = ""
    for token in report_label.split():
        if token.isdigit() and len(token) == 4:
            year = token
    return {
        "report": f"{released} {year}".strip(),
        "wheat": _row("Wheat", "bread", wheat_now, wheat_prior, "Total Use"),
        "corn": _row("Corn", "feed", corn_now, corn_prior, "Domestic Total") if corn_now else None,
        "soy": _row("Soybeans", "feed", soy_now, soy_prior, "Domestic Total") if soy_now else None,
    }


def describe(balance: dict) -> str:
    def line(row: dict | None) -> str:
        if not row or row.get("stocks_use_pct") is None:
            return ""
        change = row.get("change_pts")
        moved = "no prior month" if change is None else f"{change:+.1f} pts on the month"
        return (
            f"{row['name']} stocks are {row['stocks_mt']:.0f} million tonnes, "
            f"{row['stocks_use_pct']:.1f}% of use, {moved}."
        )

    bits = [
        f"USDA WASDE {balance.get('report') or 'latest'}.",
        line(balance.get("wheat")),
        "Wheat is the bread crop.",
        line(balance.get("corn")),
        line(balance.get("soy")),
        "Corn and soy are feed, not bread.",
        "This tile is not in the food score. A comfortable world stock does not cancel a short UK harvest.",
    ]
    return " ".join(bit for bit in bits if bit)
