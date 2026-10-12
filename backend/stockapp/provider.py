"""唯一采集连接、协议请求预算、完整分页与发布版本。"""
from contextlib import ExitStack
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
import hashlib
import inspect
import socket
import time
from zoneinfo import ZoneInfo

import baostock as bs
from baostock.util import socketutil
from baostock.common import context

from . import db, jobs, universe
from .catalog import BY_METHOD, DAILY_FIELDS, INDEX_FIELDS, PERIOD_FIELDS, MINUTE_FIELDS, REFERENCE_DATASETS, record_identity, validate_operation
from .config import settings
from .market import ready_at
from .code_changes import marker_reference
from .source_revisions import collection_revision


class SourceError(RuntimeError):
    pass


class CheckedSocket:
    def __init__(self, connection):
        self.connection = connection

    def send(self, data):
        self.connection.sendall(data)
        return len(data)

    def recv(self, length):
        data = self.connection.recv(length)
        if not data:
            raise ConnectionError("来源连接提前关闭")
        return data

    def __getattr__(self, key):
        return getattr(self.connection, key)


class Session:
    def __init__(self, job_id):
        self.job_id = job_id

    def __enter__(self):
        self.lock = db.connect()
        self.connection = self.lock.__enter__()
        acquired = self.connection.execute("SELECT pg_try_advisory_lock(81432001) AS ok").fetchone()["ok"]
        if not acquired:
            self.lock.__exit__(None, None, None)
            raise SourceError("另一个采集进程占用唯一 BaoStock 通道，请稍后重跑")
        self.original_send = socketutil.send_msg
        self.original_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(settings().provider_timeout)

        def counted_send(message):
            jobs.heartbeat(self.job_id)
            day = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            with db.connect() as conn:
                conn.execute("INSERT INTO request_usage(day) VALUES(%s) ON CONFLICT DO NOTHING", (day,))
                updated = conn.execute("UPDATE request_usage SET requests=requests+1 WHERE day=%s AND requests<%s RETURNING requests",
                                       (day, settings().request_budget)).fetchone()
                if not updated:
                    raise jobs.BudgetPaused("达到本机每日请求预算，将在上海时区次日自动续跑")
            response = self.original_send(message)
            if response is None or not response.strip():
                raise jobs.SourceRetry("BaoStock 接收为空或超时，未将部分分页当作完整数据")
            return response

        socketutil.send_msg = counted_send
        try:
            login = bs.login()
            if login.error_code != "0":
                raise SourceError(f"BaoStock 登录失败 {login.error_code}: {login.error_msg}")
            context.default_socket = CheckedSocket(context.default_socket)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        try:
            # 取消时也必须关闭 socket，logout 请求不绕过预算。
            if getattr(context, "default_socket", None) is not None:
                context.default_socket.close()
        finally:
            socketutil.send_msg = self.original_send
            socket.setdefaulttimeout(self.original_timeout)
            self.lock.__exit__(None, None, None)

    def query(self, operation):
        endpoint = BY_METHOD[operation["method"]]
        arguments = operation.get("arguments", {})
        batch = db.one("INSERT INTO data_batches(job_id,method,arguments) VALUES(%s,%s,%s) RETURNING id",
                       (self.job_id, endpoint.method, db.jb(arguments)))["id"]
        fields, data, conflict = [], [], None
        protocol_complete, unparsed_values = False, None
        try:
            result = getattr(bs, endpoint.method)(**arguments)
            if result.error_code != "0":
                raise SourceError(f"{endpoint.label} {result.error_code}: {result.error_msg}")
            fields, data = list(result.fields), []
            while result.next():
                if result.error_code != "0":
                    raise SourceError(f"分页失败 {result.error_code}: {result.error_msg}")
                values = result.get_row_data()
                if len(values) != len(fields):
                    unparsed_values = values
                    raise SourceError("来源字段数变化，未发布该批次")
                data.append(dict(zip(fields, values)))
                if len(data) % 1000 == 0:
                    jobs.heartbeat(self.job_id)
            if result.error_code != "0":
                raise SourceError(f"末页失败 {result.error_code}: {result.error_msg}")
            protocol_complete = True
            if not data and endpoint.dataset in {"bars", "bars:d", "instruments", "universe", "calendar"}:
                raise SourceError(f"{endpoint.label} 返回空；可能未就绪、源不覆盖或查询失败，未覆盖旧数据")
            staged, not_ready, non_price_markers, collection_revisions = [], 0, [], []
            received_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            for row in data:
                try:
                    dataset, key, code, period = record_identity(endpoint, arguments, row)
                except ValueError:
                    conflict = {"dataset": endpoint.dataset, "invalid_records": [row],
                                "note": "保留首次来源身份/日期/已核验版本校验失败原行；该批次没有发布"}
                    raise
                revision = collection_revision(row) if dataset == 'dividends' else None
                if revision:
                    collection_revisions.append(revision)
                marker = marker_reference(dataset, row)
                if marker:
                    non_price_markers.append(marker)
                    continue
                if dataset.startswith("bars:") or dataset in REFERENCE_DATASETS:
                    try:
                        self.validate_bar(row)
                    except SourceError:
                        conflict = {"dataset": dataset, "invalid_records": [row],
                                    "note": "保留首次校验失败的完整来源行；该批次没有发布"}
                        raise
                    expected_flag = arguments.get("adjustflag", "3")
                    if row.get("adjustflag") != expected_flag:
                        raise SourceError("返回行情复权标记与请求不一致，未发布该批次")
                    frequency = "d" if dataset in REFERENCE_DATASETS else dataset.split(":", 1)[1]
                    if ready_at(row["date"], frequency) > received_at:
                        not_ready += 1
                        continue
                digest = hashlib.sha256(db.dumps(row).encode()).hexdigest()
                staged.append((dataset, key, code, period, db.jb(row), digest, batch))
            if data and not staged and not_ready:
                raise SourceError("全部返回行情尚未达到官方就绪时刻，保留旧数据")
            universe_duplicates, universe_missing_names = {}, []
            if endpoint.dataset == "universe":
                invalid = [row for row in data if not row.get("code") or row.get("tradeStatus") not in {"0", "1"}]
                if invalid:
                    conflict = {"dataset": "universe", "invalid_records": invalid}
                    raise SourceError("证券池代码或交易状态缺失，未发布快照")
                universe_missing_names = sorted({row["code"] for row in data if not row.get("code_name")})
                grouped = {}
                for row in data:
                    grouped.setdefault(row["code"], []).append(row)
                for code, rows in grouped.items():
                    if len(rows) > 1:
                        if any(row != rows[0] for row in rows[1:]):
                            conflict = {"dataset": "universe", "code": code, "records": rows}
                            raise SourceError("证券池同一代码的名称或状态冲突，未发布快照")
                        universe_duplicates[code] = len(rows)
            with db.connect() as conn:
                # 不变内容不产生修订；旧值始终保留。发布标记与新行处于同一事务。
                conn.execute("CREATE TEMP TABLE incoming_records (dataset text,record_key text,code text,period text,payload jsonb,content_hash text,batch_id bigint) ON COMMIT DROP")
                seen = {}
                wording_variants = 0
                for values in staged:
                    natural_key = values[:2]
                    variants = seen.setdefault(natural_key, {})
                    variants[values[5]] = values
                with conn.cursor().copy("COPY incoming_records FROM STDIN") as copy:
                    for natural_key, variants in seen.items():
                        ordered = sorted(variants.values(), key=lambda value: value[5])
                        if len(ordered) > 1:
                            # 只保留税后说明文字变体；任何其他字段变化仍是冲突。
                            terms = [{k: v for k, v in value[4].obj.items()
                                      if k not in {"dividCashPsAfterTax", "dividCashStock"}} for value in ordered]
                            if natural_key[0] != "dividends" or any(term != terms[0] for term in terms[1:]):
                                conflict = {"dataset": natural_key[0], "record_key": natural_key[1],
                                            "records": [value[4].obj for value in ordered]}
                                raise SourceError("同批自然键存在相互冲突的源数据，未发布")
                            wording_variants += len(ordered) - 1
                        for index, values in enumerate(ordered):
                            key = values[1] if index == 0 else values[1] + "|source_text:" + values[5]
                            copy.write_row((values[0], key, *values[2:]))
                additions = conn.execute("WITH added AS (INSERT INTO source_records(dataset,record_key,code,period,payload,content_hash,batch_id) "
                                        "SELECT s.* FROM incoming_records s WHERE s.content_hash IS DISTINCT FROM "
                                        "(SELECT r.content_hash FROM source_records r JOIN data_batches b ON b.id=r.batch_id "
                                        "WHERE r.dataset=s.dataset AND r.record_key=s.record_key AND b.status='published' ORDER BY r.batch_id DESC LIMIT 1) "
                                        "RETURNING dataset,period) SELECT dataset,count(*) AS count,max(period) AS latest FROM added GROUP BY dataset").fetchall()
                inserted = sum(row["count"] for row in additions)
                for row in additions:
                    conn.execute("INSERT INTO dataset_stats(dataset,revisions,latest) VALUES(%s,%s,%s) ON CONFLICT(dataset) DO UPDATE "
                                 "SET revisions=dataset_stats.revisions+excluded.revisions,latest=GREATEST(dataset_stats.latest,excluded.latest)",
                                 (row["dataset"], row["count"], row["latest"]))
                conn.execute("UPDATE data_batches SET status='published',fields=%s,row_count=%s,changed_count=%s,coverage=%s,published_at=now() WHERE id=%s",
                             (db.jb(fields), len(data), inserted, db.jb({"arguments": arguments, "empty": not data, "reference_only": arguments.get("adjustflag", "3") != "3", "excluded_not_ready": not_ready, "accepted_records": len(staged), "non_price_markers": non_price_markers, "collection_only_revisions": collection_revisions, "wording_variants": wording_variants, "universe_identical_duplicates": universe_duplicates, "universe_missing_names": universe_missing_names, "validation": "完整协议分页；仅发布已就绪有效行情；已核验代码变更的精确非价格行仅原样保存于批次 non_price_markers，不写行情或填造价格；复权对照样本独立保存；全市场完整度仍需证券池对照；分红仅税后说明文字变体及逐字段对应正式公告的指定源版本分开原样保留，采集身份核验不开放账本；证券池相同重复行计数保留、成员去重；名称空值保持并标记，不影响代码/交易状态成员身份"}), batch))
                if endpoint.dataset == "universe":
                    conn.execute("INSERT INTO universe_snapshots(batch_id,day,codes) VALUES(%s,%s,%s)",
                                 (batch, arguments["day"], sorted({row["code"] for row in data})))
                conn.execute("INSERT INTO latest_quotes(code,period,payload,batch_id) "
                             "SELECT DISTINCT ON(code) code,period::date,payload,batch_id FROM incoming_records WHERE dataset='bars:d' ORDER BY code,period DESC "
                             "ON CONFLICT(code) DO UPDATE SET period=excluded.period,payload=excluded.payload,batch_id=excluded.batch_id "
                             "WHERE excluded.period>=latest_quotes.period")
                conn.execute("INSERT INTO daily_bar_index(day,code,source_id,batch_id) "
                             "SELECT period::date,code,id,batch_id FROM source_records WHERE dataset='bars:d' AND batch_id=%s "
                             "ON CONFLICT(day,code) DO UPDATE SET source_id=excluded.source_id,batch_id=excluded.batch_id "
                             "WHERE excluded.batch_id>daily_bar_index.batch_id", (batch,))
            jobs.log(self.job_id, f"{endpoint.label}：返回 {len(data)} 行，{inserted} 行新值，{not_ready} 行尚未就绪，{len(non_price_markers)} 行代码变更非价格标记，版本 {batch}")
            return batch
        except BaseException as exc:
            db.execute("UPDATE data_batches SET status='failed',fields=%s,row_count=%s,coverage=%s WHERE id=%s",
                       (db.jb(fields), len(data), db.jb({"arguments": arguments, "error": str(exc),
                        "conflict": conflict, "received_records": data,
                        "received_records_sha256": hashlib.sha256(db.dumps([{key: row[key] for key in sorted(row)} for row in data]).encode()).hexdigest(),
                        "received_records_hash_format": "SHA256 UTF-8 db.dumps：原行顺序，每行字段名排序",
                        "protocol_complete": protocol_complete, "unparsed_values": unparsed_values,
                        "validation": "失败未发布；保留已取得解析原行和首个未解析值；协议迭代完成及接收行数不代表覆盖完整"}), batch))
            raise

    @staticmethod
    def validate_bar(row):
        try:
            prices = [Decimal(row[k]) for k in ("open", "high", "low", "close")]
            if any(not p.is_finite() or p <= 0 for p in prices):
                raise ValueError("价格非正或非有限值")
            op, high, low, close = prices
            if not low <= min(op, close) <= max(op, close) <= high:
                raise ValueError("OHLC 范围异常")
            for field in ("volume", "amount"):
                value = Decimal(row.get(field) or "0")
                if not value.is_finite() or value < 0:
                    raise ValueError(f"{field} 非有限值或为负")
            reference = Decimal(row.get("preclose") or "0")
            if not reference.is_finite() or reference < 0:
                raise ValueError("前收盘参考价非有限值或为负")
            day = date.fromisoformat(row["date"])
            if day.isoformat() != row["date"]:
                raise ValueError("日期格式不是 YYYY-MM-DD")
        except (InvalidOperation, KeyError, ValueError) as exc:
            raise SourceError(f"行情异常 {row.get('code')} {row.get('date')}: {exc}") from exc


def bundle(config):
    start, end = config["start"], config["end"]
    codes = config["codes"]
    frequency = str(config.get("frequency", "d"))
    asset_types = {row["code"]: row.get("type") for row in db.payloads("instruments")}
    operations = [
        {"method": "query_stock_basic", "arguments": {}},
        {"method": "query_trade_dates", "arguments": {"start_date": start, "end_date": end}},
    ]
    for code in codes:
        asset_type = asset_types.get(code, "2" if code.startswith(("sh.000", "sz.399")) else "1")
        if asset_type == "2" and frequency in {"5", "15", "30", "60"}:
            raise ValueError(f"{code} 是指数，BaoStock 不提供指数分钟线")
        fields = (INDEX_FIELDS if asset_type in {"2", "5"} else DAILY_FIELDS) if frequency == "d" else PERIOD_FIELDS if frequency in {"w", "m"} else MINUTE_FIELDS
        operations.append({"method": "query_history_k_data_plus", "arguments": {"code": code, "fields": fields, "start_date": start, "end_date": end, "frequency": frequency, "adjustflag": "3"}})
        if asset_type == "1" and code.startswith(("sh.6", "sz.0", "sz.3")):
            operations.append({"method": "query_adjust_factor", "arguments": {"code": code, "start_date": start, "end_date": end}})
            for year in range(int(start[:4]), int(end[:4]) + 1):
                operations.append({"method": "query_dividend_data", "arguments": {"code": code, "year": str(year), "yearType": "operate"}})
    return operations


def collect(job):
    operations = job["config"].get("operations") or bundle(job["config"])
    operations = [validate_operation(o) for o in operations]
    digest = hashlib.sha256(db.dumps(operations).encode()).hexdigest()
    checkpoint = job["checkpoint"]
    if checkpoint.get("operations_digest", digest) != digest:
        raise ValueError("采集操作定义已变化，不能沿用旧检查点；请新建任务")
    legacy = checkpoint.get("completed", [])
    if legacy and sorted(legacy) != list(range(len(legacy))):
        raise ValueError("旧检查点不是连续序列，请检查任务记录")
    cursor = checkpoint.get("next_operation", len(legacy))
    if not 0 <= cursor <= len(operations):
        raise ValueError("采集检查点超出操作范围")
    jobs.heartbeat(job["id"], cursor, len(operations))
    segment_start = time.monotonic()
    with ExitStack() as stack:
        source = None
        for index in range(cursor, len(operations)):
            jobs.heartbeat(job["id"])
            operation = operations[index]
            skip = False
            if job["config"].get("universe_range") and operation["method"] == "query_all_stock":
                day = operation["arguments"]["day"]
                skip = bool(universe.snapshots(day, day, db.latest_version()))
            if not skip:
                if source is None:
                    source = stack.enter_context(Session(job["id"]))
                source.query(operation)
            jobs.heartbeat(job["id"], index+1, len(operations), {"next_operation": index+1, "operations_digest": digest})
            if index+1 < len(operations) and ((index+1-cursor) >= 100 or time.monotonic()-segment_start >= 60):
                raise jobs.CollectionSlice(f"本段已提交 {index+1-cursor} 项操作，达到 100 项或 60 秒调度阈值；让出唯一采集通道后按检查点继续")
            time.sleep(0.15)

