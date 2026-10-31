#!/usr/bin/env python3
"""Grain balance and futures curve stay out of the food score."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CurveTests(unittest.TestCase):
    def test_spread_sign(self):
        from curve import phrase, pick_curve, spread_pct

        self.assertEqual(spread_pct(100, 102), 2.0)
        self.assertEqual(spread_pct(100, 96), -4.0)
        self.assertIn("cheaper", phrase(2.2))
        self.assertIn("dearer", phrase(-3.6))

        curve = pick_curve(
            [
                {"symbol": "OLD", "price": 50, "as_of": "2026-01-01"},
                {"symbol": "FRONT", "price": 100, "as_of": "2026-10-30"},
                {"symbol": "NEXT", "price": 98, "as_of": "2026-10-30"},
            ],
            100,
        )
        self.assertIsNotNone(curve)
        assert curve is not None
        self.assertEqual(curve["front"], "FRONT")
        self.assertEqual(curve["next"], "NEXT")
        self.assertEqual(curve["curve_pct"], -2.0)

    def test_wheat_months_skip_non_delivery(self):
        from curve import candidates

        symbols = candidates("ZW", "CBT", set("HKNUZ"), date(2026, 10, 31), count=3)
        self.assertEqual(symbols[0], "ZWZ26.CBT")
        self.assertEqual(symbols[1], "ZWH27.CBT")


class GrainTests(unittest.TestCase):
    def test_latest_link_uses_year_then_month(self):
        from grain import latest_xml_url

        page = """
        <a href="https://www.usda.gov/oce/commodity/wasde/wasde1226.xml">Dec</a>
        <a href="https://www.usda.gov/oce/commodity/wasde/wasde0127.xml">Jan</a>
        """
        self.assertTrue(latest_xml_url(page).endswith("wasde0127.xml"))

    def test_grain_tile_is_outside_the_food_blend(self):
        text = (ROOT / "refresh.py").read_text()
        start = text.index("food_weights = []")
        end = text.index("weight_sum")
        self.assertNotIn("grain", text[start:end].lower())
        self.assertIn('part("World grain stocks", None', text)
