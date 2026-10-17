"""本机真实数据验收：新增两项回测、原版本重算，并核对图表与独立公式。"""
from datetime import datetime
from copy import deepcopy
import importlib.util
import json
from math import isclose
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener
from zoneinfo import ZoneInfo

import pandas as pd

from stockapp import db, plugins
from stockapp.config import ROOT, settings
from stockapp.market import price_series

BASE = f"http://127.0.0.1:{settings().api_port}/api"
OPENER = build_opener(ProxyHandler({}))
CODES = ["sh.600000", "sz.000001", "sh.600519"]
START, END = "2024-06-01", "2026-10-09"
RECEIPT = ROOT / "runtime/rvc001-v1.1-runs.json"


def api(path, body=None):
    request = Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json", "X-Stock-Local": "1"})
    with OPENER.open(request, timeout=60) as response:
        return json.load(response)


def save(state):
    RECEIPT.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wait(run_id):
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        row = db.one("SELECT status,error FROM jobs WHERE id=%s", (run_id,))
        if row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            assert row["status"] == "completed", row
            return api(f"/backtests/{run_id}")
        time.sleep(1)
    raise TimeoutError(run_id)


def chart(code, version, **options):
    params = {"frequency": "d", "start": START, "end": END, "version": version, "limit": 20000,
              "adjustment": "raw", **options}
    return api(f"/bars/{code}?" + urlencode(params))


def main():
    if RECEIPT.exists():
        state = json.loads(RECEIPT.read_text("utf-8"))
    else:
        models = api("/strategies")
        previous = json.loads((ROOT / "runtime/rvc001-runs.json").read_text("utf-8"))
        state = {"runs": {}, "replays": {}, "old_rvc_originals": previous["runs"], "old_rvc_replays": {}}
        for strategy_id in ("alei_red_change", "alei_green_change"):
            model = next(row for row in models if row["id"] == strategy_id and row["enabled"])
            assert model["version"] == ("1.1.0" if strategy_id == "alei_red_change" else "1.0.0")
            config = {"strategy_id": strategy_id, "strategy_version": model["version"], "strategy_digest": model["digest"],
                      "codes": CODES, "start": START, "end": END, "parameters": {"exposure": 0.95}}
            state["runs"][strategy_id] = api("/backtests", config)["job_id"]
        baseline = db.one("SELECT r.id FROM backtest_runs r JOIN jobs j ON j.id=r.id "
                          "WHERE j.status='completed' AND r.manifest->'config'->>'strategy_id'='ma_cross' "
                          "AND r.manifest->>'engine_version'='cash-ledger-0.12.1' ORDER BY j.created_at DESC LIMIT 1")
        assert baseline, "需要一份已有经典策略报告核对旧引擎重算"
        state["classic_original"] = str(baseline["id"])
        save(state)
    originals = {key: wait(run_id) for key, run_id in state["runs"].items()}
    versions = {report["manifest"]["data_version"] for report in originals.values()}
    assert len(versions) == 1
    version = versions.pop()
    assert all(report["manifest"]["engine_version"] == "cash-ledger-0.13.1" for report in originals.values())
    for key, run_id in state["runs"].items():
        if key not in state["replays"]:
            state["replays"][key] = api(f"/backtests/{run_id}/replay", {})["job_id"]
            save(state)
    if "classic_replay" not in state:
        state["classic_replay"] = api(f"/backtests/{state['classic_original']}/replay", {})["job_id"]
        save(state)
    for key, run_id in state["old_rvc_originals"].items():
        if key not in state["old_rvc_replays"]:
            state["old_rvc_replays"][key] = api(f"/backtests/{run_id}/replay", {})["job_id"]
            save(state)
    replays = {key: wait(run_id) for key, run_id in state["replays"].items()}
    legacy_reports = {}
    for key, run_id in state["old_rvc_originals"].items():
        original = api(f"/backtests/{run_id}")
        replay = wait(state["old_rvc_replays"][key])
        assert original["manifest"]["engine_version"] == "cash-ledger-0.13.0"
        assert original["manifest"] == replay["manifest"] and original["result"] == replay["result"]
        legacy_reports[key] = original
    classic = api(f"/backtests/{state['classic_original']}")
    classic_replay = wait(state["classic_replay"])
    assert classic_replay["result"] == classic["result"]
    assert classic_replay["manifest"] == classic["manifest"]
    cases, fixtures, expected_orders = [], [], {key: set() for key in originals}
    calendar = [row["calendar_date"] for row in db.payloads("calendar", end=END, version=version)
                if row["is_trading_day"] == "1" and START <= row["calendar_date"] <= END]
    next_day = dict(zip(calendar, calendar[1:]))
    for code in CODES:
        history = price_series(code, version=version, end=END)
        closes = pd.Series([bar["signal_close"] for bar in history])
        expected_fast = closes.ewm(span=3, adjust=False).mean()
        expected_slow = closes.ewm(span=8, adjust=False).mean()
        expected_mid = closes.ewm(span=10, adjust=False).mean()
        expected_sd = closes.rolling(10).std(ddof=1)
        values = chart(code, version)
        fixtures.append({"code": code, "bars": values["data"]})
        by_date = {bar["date"]: bar for bar in values["data"]}
        targets = {key: 0 for key in originals}
        counts = {"buy": 0, "red_exit": 0, "pink_exit": 0, "green_exit": 0, "down_cross": 0}
        for index, bar in enumerate(history):
            if bar["date"] not in by_date:
                continue
            value = by_date[bar["date"]]["rvc001"]
            for field, expected in (("fast", expected_fast[index]), ("slow", expected_slow[index]),
                                    ("mid", expected_mid[index]), ("sd", expected_sd[index])):
                assert isclose(value[field], expected, rel_tol=1e-10, abs_tol=1e-10), (code, bar["date"], field)
            previous = history[index - 1]
            fast, slow, prev_fast, prev_slow = (bar["signal_ema3"], bar["signal_ema8"],
                                               previous["signal_ema3"], previous["signal_ema8"])
            color = ("red" if fast >= prev_fast else "pink") if fast >= slow else ("blue" if fast > prev_fast else "green")
            buy = fast > slow and prev_fast <= prev_slow
            assert value["state"] == color and value["buy"] == buy
            for key, exit_states, marker in (("alei_red_change", ("pink", "green"), "red_exit"),
                                             ("alei_green_change", ("green",), "green_exit")):
                previous_target = targets[key]
                sell = previous_target > 0 and color in exit_states
                assert value[marker] == sell
                targets[key] = 0 if sell else previous_target if previous_target > 0 else 0.95 / len(CODES) if buy else 0
                if targets[key] != previous_target:
                    expected_orders[key].add((code, bar["date"], "sell" if sell else "buy"))
            assert value["pink_exit"] == (value["red_exit"] and color == "pink")
            for field in counts:
                counts[field] += int(value[field])
            assert isclose(value["open"], bar["open"] * bar["signal_scale"])
            assert isclose(value["close"], bar["signal_close"])
        for adjustment in ("forward", "backward"):
            adjusted = chart(code, version, adjustment=adjustment)
            assert [bar["rvc001"] for bar in adjusted["data"]] == [bar["rvc001"] for bar in values["data"]]
        limited = chart(code, version, limit=100)
        assert limited["data"] == values["data"][-100:]
        shifted = chart(code, version, start="2025-01-01")
        for bar in shifted["data"]:
            for field in ("fast", "slow", "mid", "sd", "state", "buy", "down_cross", "up", "down", "up1", "down1"):
                assert bar["rvc001"][field] == by_date[bar["date"]]["rvc001"][field]
        cases.append({"code": code, "bars": len(values["data"]), "markers": counts,
                      "independent_ema_sd_equal": True, "adjustment_invariant": True, "limit_and_window_invariant": True})
    legacy_cases = []
    for key, report in legacy_reports.items():
        archived = Path(report["manifest"]["engine_source"]["package"]) / "stockapp/rvc001.py"
        spec = importlib.util.spec_from_file_location("legacy_rvc001", archived)
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        legacy_version = report["manifest"]["data_version"]
        for code in CODES:
            original_bars = deepcopy(price_series(code, version=legacy_version, end=END))
            legacy.attach_chart_indicator(original_bars, START)
            expected = {bar["date"]: bar["rvc001"] for bar in original_bars if START <= bar["date"] <= END}
            actual = chart(code, legacy_version, rvc001_red_exit="pink")
            assert actual["rvc001_red_exit"] == "pink"
            for bar in actual["data"]:
                for field, value in expected[bar["date"]].items():
                    assert bar["rvc001"][field] == value, (key, code, bar["date"], field)
            legacy_cases.append({"strategy": key, "code": code, "bars": len(actual["data"]),
                                 "original_indicator_and_exit_markers_equal": True})
    models = api("/strategies")
    old_red = next(row for row in models if row["id"] == "alei_red_change" and row["version"] == "1.0.0")
    assert not old_red["enabled"]
    assert next(row for row in models if row["id"] == "alei_green_change" and row["enabled"])["digest"] == legacy_reports["alei_green_change"]["manifest"]["strategy"]["digest"]
    reports = []
    for key, report in originals.items():
        result = report["result"]
        assert result == replays[key]["result"] and report["manifest"] == replays[key]["manifest"]
        assert api(f"/backtests/{report['id']}/export")["result"] == result
        assert [event["valuation_date"] for event in result["signal_events"]] == calendar
        for order in result["orders"]:
            assert (order["code"], order["signal_date"], order["side"]) in expected_orders[key], order
            if order["execution_date"]:
                assert order["execution_date"] == next_day[order["signal_date"]], order
        archived = report["manifest"]["engine_source"]
        assert plugins.digest_directory(Path(archived["package"]) / "stockapp") == archived["digest"]
        assert (Path(archived["package"]) / "stockapp/rvc001.py").is_file()
        reports.append({"strategy": key, "name": report["manifest"]["strategy"]["manifest"]["name"],
                        "run_id": report["id"], "replay_id": replays[key]["id"], "engine_version": report["manifest"]["engine_version"],
                        "model_digest": report["manifest"]["strategy"]["digest"], "engine_digest": archived["digest"],
                        "signals": len(result["signal_events"]), "orders": len(result["orders"]), "trades": len(result["trades"]),
                        "metrics": result["metrics"], "all_order_signals_and_next_day_execution_equal": True,
                        "frozen_replay_and_export_equal": True})
    proof = {"checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "data_version": version,
             "start": START, "end": END, "codes": CODES, "reports": reports, "chart_cases": cases,
             "classic_original": state["classic_original"], "classic_replay": state["classic_replay"],
             "classic_original_manifest_and_result_equal": True, "offline_tests": 25,
             "old_rvc_originals": state["old_rvc_originals"], "old_rvc_replays": state["old_rvc_replays"],
             "old_rvc_manifest_and_result_equal": True, "legacy_chart_cases": legacy_cases,
             "old_red_disabled_new_red_1_1_enabled": True, "green_model_digest_unchanged": True,
             "scope": "红变1.1.0首次粉/绿K出场，绿变1.0.0仍只在绿K出场；真实已入库行情，新运行、旧RVC及经典报告冻结重算和旧副图标记核对。没有调用行情SDK、写入模拟行情或实施实盘交易。"}
    (ROOT / "runtime/rvc001-chart-inputs.json").write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")
    (ROOT / "docs/verification-rvc001-v1.1.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": True, "data_version": version, "reports": reports, "classic_replay_equal": True}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
