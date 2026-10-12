r"""离线回归：数据库/行情均模拟，归档只写入临时目录。

运行：$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m unittest discover -s tests -v
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import MagicMock, patch

os.environ["STOCK_DB_PASSWORD"] = "offline-regression-only"
os.environ["STOCK_API_PORT"] = "8010"

from stockapp import api, db, engine, experiments, plugins, provider
from stockapp.config import ROOT, Settings


async def request(path, method="GET", body=None, query="", origin=None):
    content = json.dumps(body).encode() if body is not None else b""
    headers = [(b"host", b"127.0.0.1:8010"), (b"x-stock-local", b"1"), (b"content-type", b"application/json")]
    if origin:
        headers.append((b"origin", origin.encode()))
    if method == "OPTIONS":
        headers.append((b"access-control-request-method", b"POST"))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "headers": headers, "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8010)}
    sent = False
    messages = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": content, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    await api.app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, raw


class BackfillTests(unittest.TestCase):
    def test_partial_and_concurrent_submissions_reuse_range(self):
        stored = []
        lock = threading.Lock()
        missing = ["2026-01-05", "2026-01-06"]

        @contextmanager
        def connect():
            conn = MagicMock()
            acquired = False

            def execute(sql, params):
                nonlocal acquired
                result = MagicMock()
                if "pg_advisory_xact_lock" in sql:
                    lock.acquire()
                    acquired = True
                elif sql.startswith("SELECT id FROM jobs"):
                    identity = params[0].obj
                    result.fetchone.return_value = next(({"id": job[0]} for job in stored if job[1]["universe_range"] == identity), None)
                elif sql.startswith("INSERT INTO jobs"):
                    stored.append((params[0], params[1].obj))
                else:
                    self.fail(sql)
                return result

            conn.execute.side_effect = execute
            try:
                yield conn
            finally:
                if acquired:
                    lock.release()

        model = api.UniverseRange(start="2026-01-05", end="2026-01-06")
        with patch.object(db, "connect", connect), patch.object(db, "latest_version", return_value=7), \
                patch.object(api.universe, "coverage", side_effect=lambda *args: {"missing_days": list(missing)}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: api.backfill_universe(model), range(2)))
            self.assertEqual(results[0]["job_id"], results[1]["job_id"])
            missing.pop(0)
            self.assertEqual(api.backfill_universe(model)["job_id"], results[0]["job_id"])
            missing.clear()
            self.assertEqual(api.backfill_universe(model)["job_id"], results[0]["job_id"])
            stored.clear()
            self.assertIsNone(api.backfill_universe(model)["job_id"])

    def test_published_snapshots_skipped_and_checkpoint_advances(self):
        operations = [{"method": "query_all_stock", "arguments": {"day": day}} for day in ("2026-01-05", "2026-01-06")]
        job = {"id": "offline", "config": {"operations": operations, "universe_range": {"start": "2026-01-05", "end": "2026-01-06"}}, "checkpoint": {}}
        with patch.object(provider, "Session") as session, patch.object(provider.jobs, "heartbeat") as heartbeat, \
                patch.object(db, "latest_version", return_value=9), patch.object(provider.universe, "snapshots", side_effect=lambda day, *_: [day] if day == "2026-01-05" else []), \
                patch.object(provider.time, "sleep"):
            provider.collect(job)
            session.return_value.__enter__.return_value.query.assert_called_once_with(operations[1])
            self.assertEqual(heartbeat.call_args.args[1:3], (2, 2))
            job["checkpoint"] = heartbeat.call_args.args[3]
            session.reset_mock()
            provider.collect(job)
            session.assert_not_called()

    def test_all_published_never_opens_source_and_manual_refresh_remains(self):
        operation = {"method": "query_all_stock", "arguments": {"day": "2026-01-05"}}
        job = {"id": "offline", "config": {"operations": [operation], "universe_range": {"start": "2026-01-05", "end": "2026-01-05"}}, "checkpoint": {}}
        with patch.object(provider, "Session") as session, patch.object(provider.jobs, "heartbeat"), \
                patch.object(db, "latest_version", return_value=9), patch.object(provider.universe, "snapshots", return_value=[{}]), patch.object(provider.time, "sleep"):
            provider.collect(job)
            session.assert_not_called()
            del job["config"]["universe_range"]
            provider.collect(job)
            session.return_value.__enter__.return_value.query.assert_called_once_with(operation)


class ArchiveTests(unittest.TestCase):
    def test_concurrent_strategy_installs_publish_one_complete_archive(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "manifest.json").write_bytes((ROOT / "strategies/ma_cross/manifest.json").read_bytes())
            (source / "strategy.py").write_bytes((ROOT / "strategies/ma_cross/strategy.py").read_bytes())
            barrier = threading.Barrier(2)
            original = plugins.shutil.copytree

            def copytree(*args, **kwargs):
                barrier.wait(timeout=10)
                return original(*args, **kwargs)

            with patch.object(plugins, "ROOT", root), patch.object(db, "execute") as writes, patch.object(plugins.shutil, "copytree", side_effect=copytree):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: plugins.install(source), range(2)))
                self.assertEqual(results[0], results[1])
                target = Path(writes.call_args.args[1][-1])
                self.assertEqual(plugins.digest_directory(target), results[0]["digest"])
                self.assertEqual(list(target.parent.glob(".archive-*")), [])
                (target / "strategy.py").write_text("changed", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "摘要变化"):
                    plugins.install(source)

    def test_concurrent_engine_freezes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "backend/stockapp"
            source.mkdir(parents=True)
            (source / "engine.py").write_text("ENGINE_VERSION = 'fixture'", encoding="utf-8")
            original = plugins.shutil.copytree
            barrier = threading.Barrier(2)

            def copytree(*args, **kwargs):
                barrier.wait(timeout=10)
                return original(*args, **kwargs)

            with patch.object(engine, "ROOT", root), patch.object(engine, "manifest", side_effect=lambda config: {"config": config}), \
                    patch.object(plugins.shutil, "copytree", side_effect=copytree):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: engine.freeze({}), range(2)))
                self.assertEqual(results[0]["engine_source"], results[1]["engine_source"])
                archive = Path(results[0]["engine_source"]["package"]) / "stockapp"
                self.assertEqual(plugins.digest_directory(archive), results[0]["engine_source"]["digest"])

    def test_changed_source_is_not_published(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            file = source / "strategy.py"
            file.write_text("before", encoding="utf-8")
            digest = plugins.digest_directory(source)
            file.write_text("after", encoding="utf-8")
            target = root / "archive"
            with self.assertRaisesRegex(ValueError, "复制期间"):
                plugins.archive_directory(source, target, digest)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".archive-*")), [])


class ExperimentTests(unittest.TestCase):
    def test_all_stage_bounds_checked_before_persistence(self):
        manifest = json.loads((ROOT / "strategies/ma_cross/manifest.json").read_text(encoding="utf-8"))
        intervals = {"train": {"start": "2026-01-05", "end": "2026-03-31"},
                     "validation": {"start": "2026-04-01", "end": "2026-06-30"},
                     "out_of_sample": {"start": "2026-07-01", "end": "2026-10-09"}}
        for code, asset_type in (("sh.510050", "5"), ("sz.300750", "1")):
            config = {"strategy_id": manifest["id"], "strategy_version": manifest["version"], "strategy_digest": "fixture", "codes": [code], "parameters": {}}
            with self.subTest(code=code), patch.object(engine, "freeze", side_effect=engine.manifest), \
                    patch.object(db, "one", return_value={"manifest": manifest}), patch.object(db, "latest_version", return_value=12), \
                    patch.object(db, "payloads", return_value=[{"code": code, "type": asset_type}]) as payloads, \
                    patch.object(engine, "verify_reference_documents"), patch.object(engine, "supplemental_actions", return_value=[]), \
                    patch.object(engine, "growth_documents", return_value=[]), patch.object(engine.importlib.metadata, "distributions", return_value=[]), \
                    patch.object(db, "connect") as connect, patch.object(engine, "persist_run", return_value="run") as persist:
                for stage in ("validation", "out_of_sample"):
                    invalid = deepcopy(intervals)
                    invalid[stage]["end"] = "2026-10-10"
                    if stage == "validation":
                        invalid["out_of_sample"] = {"start": "2026-10-11", "end": "2026-10-12"}
                    with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, "核验.*区间"):
                        experiments.create("fixture", config, invalid, [{}])
                    connect.assert_not_called()
                    persist.assert_not_called()
                experiments.create("fixture", config, intervals, [{}])
                payloads.assert_called_with("instruments", version=12)
                self.assertEqual(persist.call_count, 2)
                for stage, call in zip(("train", "validation"), persist.call_args_list):
                    frozen = call.args[1]
                    self.assertEqual(frozen["data_version"], 12)
                    self.assertEqual({k: frozen["config"][k] for k in ("start", "end")}, intervals[stage])
                    self.assertIn(code, frozen["asset_rules"])


class ApiTests(unittest.TestCase):
    def test_invalid_strategy_paths_return_400_without_registration(self):
        with TemporaryDirectory() as temporary, patch.object(db, "execute") as writes:
            root = Path(temporary)
            cases = [(root / "missing", "存在的目录"), (root, "manifest.json")]
            for path, detail in cases:
                status, raw = asyncio.run(request("/api/strategies/install", "POST", {"path": str(path)}))
                self.assertEqual(status, 400)
                self.assertIn(detail, json.loads(raw)["detail"])
            (root / "manifest.json").write_bytes((ROOT / "strategies/ma_cross/manifest.json").read_bytes())
            status, raw = asyncio.run(request("/api/strategies/install", "POST", {"path": str(root)}))
            self.assertEqual(status, 400)
            self.assertIn("strategy.py", json.loads(raw)["detail"])
            writes.assert_not_called()

    def test_default_end_advances_at_shanghai_midnight(self):
        with patch.object(api, "datetime") as clock, patch.object(api, "chart", return_value={}) as chart:
            clock.now.side_effect = [datetime(2026, 10, 12, 23, 59), datetime(2026, 10, 13, 0, 1)]
            for expected in ("2026-10-12", "2026-10-13"):
                self.assertEqual(asyncio.run(request("/api/bars/sh.600000"))[0], 200)
                self.assertEqual(chart.call_args.args[3], expected)
                self.assertEqual(clock.now.call_args.args[0].key, "Asia/Shanghai")
            self.assertEqual(asyncio.run(request("/api/bars/sh.600000", query="end=2026-10-09"))[0], 200)
            self.assertEqual(chart.call_args.args[3], "2026-10-09")
            self.assertEqual(clock.now.call_count, 2)

    def test_configured_port_allowed_for_cors_and_management(self):
        with patch.object(db, "execute") as writes:
            self.assertEqual(asyncio.run(request("/api/sync", "POST", {}, origin="http://127.0.0.1:8010"))[0], 200)
            self.assertEqual(asyncio.run(request("/api/sync", "OPTIONS", origin="http://localhost:8010"))[0], 200)
            self.assertEqual(asyncio.run(request("/api/sync", "POST", {}, origin="http://127.0.0.1:8000"))[0], 403)
            self.assertEqual(asyncio.run(request("/api/sync", "OPTIONS", origin="http://localhost:8000"))[0], 400)
            self.assertEqual(writes.call_count, 1)

    def test_port_defaults_and_validation(self):
        with patch.dict(os.environ):
            os.environ.pop("STOCK_API_PORT", None)
            self.assertEqual(Settings(_env_file=None, db_password="fixture").api_port, 8000)
            self.assertEqual(Settings(_env_file=None, db_password="fixture", api_port=8010).api_port, 8010)
            for port in (0, 65536):
                with self.assertRaises(ValueError):
                    Settings(_env_file=None, db_password="fixture", api_port=port)


if __name__ == "__main__":
    unittest.main()
