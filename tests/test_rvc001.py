"""离线验证 RVC001 公式、策略状态和图表价格口径，不写入行情库。"""
import asyncio
from copy import deepcopy
from datetime import date, timedelta
import json
from math import sin
from pathlib import Path
from statistics import stdev
import unittest
from unittest.mock import patch

import pandas as pd

from stockapp import market, plugins, rvc001

ROOT = Path(__file__).resolve().parents[1]


def source_rows(size=80):
    rows = []
    previous = 10
    for index in range(size):
        scale = 0.5 if index >= 35 else 1
        close = (10 + sin(index / 3)) * scale
        preclose = previous * (0.5 if index == 35 else 1)
        rows.append({"date": (date(2024, 1, 1) + timedelta(days=index)).isoformat(),
                     "open": str(close - 0.03), "close": str(close), "high": str(close + 0.1),
                     "low": str(close - 0.1), "preclose": str(preclose), "volume": "10000"})
        previous = close
    return rows


def fixture_history(fast, slow, previous_fast, previous_slow):
    return [{"signal_ema3": previous_fast, "signal_ema8": previous_slow}] * 10 + [
        {"signal_ema3": fast, "signal_ema8": slow}]


class FormulaTests(unittest.TestCase):
    def test_ema_initialization_and_reference_recursion(self):
        self.assertEqual(rvc001.ema(None, 12, 3), 12)
        self.assertEqual(rvc001.ema(10, 12, 3), 11)
        rows = source_rows()
        with patch.object(market.db, "payloads", return_value=rows):
            bars = market.price_series("fixture")
        for period in (3, 8):
            reference = pd.Series([b["signal_close"] for b in bars]).ewm(span=period, adjust=False).mean()
            for bar, expected in zip(bars, reference):
                self.assertAlmostEqual(bar[f"signal_ema{period}"], expected, places=12)

    def test_all_colors_and_equality_boundaries(self):
        for values, state in [((10, 9, 9, 9), "red"), ((10, 10, 10, 10), "red"),
                              ((10, 10, 11, 9), "pink"), ((9, 10, 8, 10), "blue"),
                              ((9, 10, 9, 10), "green"), ((9, 10, 10, 10), "green")]:
            with self.subTest(values=values):
                self.assertEqual(rvc001.trend(*values)["state"], state)
        self.assertEqual(rvc001.trend(10, 10, None, None),
                         {"state": None, "buy": False, "down_cross": False})

    def test_crosses_require_strict_current_cross_and_allow_previous_equality(self):
        self.assertTrue(rvc001.trend(11, 10, 10, 10)["buy"])
        self.assertFalse(rvc001.trend(10, 10, 9, 10)["buy"])
        self.assertFalse(rvc001.trend(11, 10, 11, 10)["buy"])
        self.assertTrue(rvc001.trend(9, 10, 10, 10)["down_cross"])
        self.assertFalse(rvc001.trend(10, 10, 11, 10)["down_cross"])

    def test_tracks_warmup_sample_sd_and_constant_prices(self):
        with patch.object(market.db, "payloads", return_value=source_rows(20)):
            bars = market.price_series("fixture")
        rvc001.attach_chart_indicator(bars, bars[0]["date"])
        self.assertTrue(all(bar["rvc001"]["up"] is None for bar in bars[:9]))
        for index, bar in enumerate(bars[9:], 9):
            value = bar["rvc001"]
            self.assertAlmostEqual(value["sd"], stdev(b["signal_close"] for b in bars[index-9:index+1]))
            self.assertAlmostEqual(value["up1"] - value["mid"], 2 * value["sd"])
            self.assertAlmostEqual(value["mid"] - value["down"], value["sd"])
        flat = [{"date": "2024-01-01", "signal_close": 10, "signal_scale": 1,
                 "signal_ema3": 10, "signal_ema8": 10, "open": 10, "low": 10, "high": 10} for _ in range(11)]
        rvc001.attach_chart_indicator(flat, "2024-01-01")
        self.assertEqual(flat[-1]["rvc001"]["sd"], 0)
        self.assertEqual(flat[-1]["rvc001"]["up1"], 10)
        self.assertFalse(any(b["rvc001"]["buy"] for b in flat))


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.models = {}
        for strategy_id in ("alei_red_change", "alei_green_change"):
            directory = ROOT / "strategies" / strategy_id
            manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
            plugins.validate_manifest(manifest)
            cls = plugins.load({"source_path": str(directory), "digest": plugins.digest_directory(directory), "manifest": manifest})
            self.models[strategy_id] = cls(plugins.validate_parameters(manifest, {}))

    def test_named_manifests_are_daily_and_expose_only_exposure(self):
        for strategy_id, name in (("alei_red_change", "阿雷红变策略"), ("alei_green_change", "阿雷绿变策略")):
            manifest = json.loads((ROOT / "strategies" / strategy_id / "manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["name"], name)
            self.assertEqual(manifest["frequency"], "d")
            self.assertEqual(manifest["lookback"], 11)
            self.assertEqual(set(manifest["parameters"]), {"exposure"})
            self.assertEqual(manifest["version"], "1.1.0" if strategy_id == "alei_red_change" else "1.0.0")

    def test_entries_distinct_exits_and_no_reentry_without_cross(self):
        context = {"codes": ["a", "b"], "history": {"a": fixture_history(11, 10, 10, 10),
                   "b": fixture_history(11, 10, 11, 10)}, "targets": {}, "first_signal": True}
        for model in self.models.values():
            self.assertEqual(model.on_bar(context), {"a": 0.475, "b": 0})
        context["targets"] = {"a": 0.475, "b": 0}
        context["history"]["a"] = fixture_history(10.5, 10, 11, 10)
        self.assertEqual(self.models["alei_red_change"].on_bar(context)["a"], 0)
        self.assertEqual(self.models["alei_green_change"].on_bar(context)["a"], 0.475)
        context["history"]["a"] = fixture_history(9, 10, 11, 10)
        self.assertEqual(self.models["alei_red_change"].on_bar(context)["a"], 0)
        self.assertEqual(self.models["alei_green_change"].on_bar(context)["a"], 0)
        context["targets"]["a"] = 0
        context["history"]["a"] = fixture_history(11, 10, 11, 10)
        for model in self.models.values():
            self.assertEqual(model.on_bar(context)["a"], 0)

    def test_repeated_buys_do_not_rebalance_and_warmup_fails(self):
        context = {"codes": ["a"], "history": {"a": fixture_history(11, 10, 9, 10)}, "targets": {"a": 0.4}}
        for model in self.models.values():
            self.assertEqual(model.on_bar(context), {"a": 0.4})
            with self.assertRaisesRegex(ValueError, "预热"):
                model.on_bar({**context, "history": {"a": context["history"]["a"][-2:]}})

    def test_red_exits_after_blue_and_green_holds_consecutive_pink(self):
        context = {"codes": ["a"], "history": {"a": fixture_history(9, 10, 8, 10)}, "targets": {"a": 0.95}}
        for model in self.models.values():
            self.assertEqual(model.on_bar(context), {"a": 0.95})
        context["history"]["a"] = fixture_history(8, 10, 9, 10)
        for model in self.models.values():
            self.assertEqual(model.on_bar(context), {"a": 0})
            self.assertEqual(model.on_bar({**context, "targets": {"a": 0}}), {"a": 0})
        for values in ((11, 10, 12, 10), (10.5, 10, 11, 10), (10, 10, 10.5, 10)):
            context["history"]["a"] = fixture_history(*values)
            self.assertEqual(self.models["alei_green_change"].on_bar(context), {"a": 0.95})
            self.assertEqual(self.models["alei_red_change"].on_bar(context), {"a": 0})

    def test_legacy_single_color_call_still_waits_for_pink(self):
        context = {"codes": ["a"], "history": {"a": fixture_history(9, 10, 11, 10)}, "targets": {"a": 0.95}}
        self.assertEqual(rvc001.strategy_targets(context, 0.95, "pink"), {"a": 0.95})
        self.assertEqual(rvc001.strategy_targets(context, 0.95, ("pink", "green")), {"a": 0})


class ChartTests(unittest.TestCase):
    def chart(self, rows, start, end, adjustment="raw", limit=10000, frequency="d", red_exit_mode="pink_or_green"):
        with patch.object(market.db, "payloads", return_value=deepcopy(rows)), patch.object(market.db, "one", return_value=None):
            return market.chart("fixture", frequency, start, end, adjustment, limit=limit, version=7, rvc001_red_exit=red_exit_mode)

    def test_window_limit_adjustment_and_future_invariance(self):
        rows = source_rows()
        whole = self.chart(rows, rows[0]["date"], rows[-1]["date"])
        clipped = self.chart(rows, rows[15]["date"], rows[-1]["date"], limit=20)
        adjusted = self.chart(rows, rows[0]["date"], rows[-1]["date"], adjustment="forward")
        future_changed = deepcopy(rows)
        for row in future_changed[50:]:
            row["close"] = str(float(row["close"]) * 2)
        changed = self.chart(future_changed, rows[0]["date"], rows[-1]["date"])
        self.assertEqual([b["rvc001"] for b in whole["data"]], [b["rvc001"] for b in adjusted["data"]])
        self.assertEqual(whole["data"][:50], changed["data"][:50])
        reference = {b["date"]: b["rvc001"] for b in whole["data"]}
        for bar in clipped["data"]:
            for field in ("fast", "slow", "mid", "sd", "state", "buy", "down_cross", "open", "close", "up1"):
                self.assertEqual(bar["rvc001"][field], reference[bar["date"]][field])
        limited = self.chart(rows, rows[0]["date"], rows[-1]["date"], limit=20)
        self.assertEqual(limited["data"], whole["data"][-20:])
        self.assertTrue(limited["truncated"])

    def test_chart_exits_match_targets_from_request_start(self):
        rows = source_rows()
        with patch.object(market.db, "payloads", return_value=rows):
            history = market.price_series("fixture")
        start = rows[20]["date"]
        result = self.chart(rows, start, rows[-1]["date"])
        targets = {"red": 0, "green": 0}
        for index, bar in enumerate(result["data"], 20):
            context = {"codes": ["fixture"], "history": {"fixture": history[index-10:index+1]}}
            for key, states, marker in (("red", ("pink", "green"), "red_exit"), ("green", "green", "green_exit")):
                before = targets[key]
                targets[key] = rvc001.strategy_targets({**context, "targets": {"fixture": before}}, 0.95, states)["fixture"]
                self.assertEqual(bar["rvc001"][marker], before > 0 and targets[key] == 0)
            self.assertEqual(bar["rvc001"]["pink_exit"], bar["rvc001"]["red_exit"] and bar["rvc001"]["state"] == "pink")

    def test_direct_green_exit_and_legacy_chart_mode(self):
        rows = source_rows(14)
        previous = 10
        for row, close in zip(rows, [10] * 11 + [11, 8, 7]):
            row.update(open=str(close), close=str(close), low=str(close), high=str(close), preclose=str(previous))
            previous = close
        current = self.chart(rows, rows[0]["date"], rows[-1]["date"])
        legacy = self.chart(rows, rows[0]["date"], rows[-1]["date"], red_exit_mode="pink")
        self.assertTrue(current["data"][11]["rvc001"]["buy"])
        self.assertTrue(current["data"][12]["rvc001"]["red_exit"])
        self.assertTrue(current["data"][12]["rvc001"]["green_exit"])
        self.assertFalse(current["data"][12]["rvc001"]["pink_exit"])
        self.assertFalse(legacy["data"][12]["rvc001"]["red_exit"])
        self.assertFalse(current["data"][13]["rvc001"]["red_exit"])
        self.assertEqual(legacy["rvc001_red_exit"], "pink")
        self.assertIn("旧版红变", legacy["rvc001_note"])
        for new, old in zip(current["data"], legacy["data"]):
            for field in ("fast", "slow", "mid", "sd", "state", "buy", "down_cross", "green_exit"):
                self.assertEqual(new["rvc001"][field], old["rvc001"][field])

    def test_all_chart_frequencies_and_empty_data(self):
        for frequency in ("d", "w", "m", "5", "15", "30", "60"):
            with self.subTest(frequency=frequency):
                rows = source_rows(20)
                result = self.chart(rows, rows[0]["date"], rows[-1]["date"], frequency=frequency)
                self.assertEqual(len(result["data"]), 20)
                self.assertIn("rvc001", result["data"][-1])
        self.assertEqual(self.chart([], "2024-01-01", "2024-02-01")["data"], [])


class ChartApiTests(unittest.TestCase):
    def test_modes_reach_chart_and_invalid_mode_is_rejected(self):
        from test_regressions import api, request
        with patch.object(api, "chart", return_value={"data": []}) as chart:
            for query, expected in (("", "pink_or_green"), ("rvc001_red_exit=pink", "pink"),
                                    ("rvc001_red_exit=pink_or_green", "pink_or_green")):
                status, _ = asyncio.run(request("/api/bars/sh.600000", query=query))
                self.assertEqual(status, 200)
                self.assertEqual(chart.call_args.args[-1], expected)
            chart.reset_mock()
            status, _ = asyncio.run(request("/api/bars/sh.600000", query="rvc001_red_exit=unknown"))
            self.assertEqual(status, 422)
            chart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
