from datetime import date, datetime
import json
import csv
from io import StringIO
from pathlib import Path
import re
from typing import Literal
from uuid import UUID
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db, jobs, plugins, experiments, universe
from .catalog import catalogue, BY_METHOD, REFERENCE_DATASETS, validate_operation
from .config import ROOT, settings
from .engine import submit_backtest, replay
from .market import chart
from .market_summary import summarize
from .macro import MACRO_POLICIES, describe as describe_macro, policy as macro_policy
from .interface_support import capabilities
from .asset_rules import ETF_CODES, ETF_START, ETF_END, GROWTH_END
from .screening import MarketFilter, search
from .code_changes import REFERENCE as CODE_CHANGE_REFERENCE
from .source_revisions import ZHONGYAN_LIMIT

app = FastAPI(title="阿雷 · 本地量化", version="0.1.0")
origins = {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (5173, settings().api_port)}
app.add_middleware(CORSMiddleware, allow_origins=sorted(origins), allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-Stock-Local"])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "::1", "testserver"])


@app.middleware("http")
async def local_requests(request: Request, call_next):
    if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
        if request.headers.get("origin") not in origins | {None} or request.headers.get("x-stock-local") != "1":
            return JSONResponse({"detail": "仅接受本机平台提交的管理请求"}, status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def invalid_input(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


class CollectRequest(BaseModel):
    codes: list[str] = Field(default_factory=list, max_length=10000)
    start: date = date(2023, 1, 1)
    end: date = Field(default_factory=date.today)
    frequency: Literal["d", "w", "m", "5", "15", "30", "60"] = "d"
    operations: list[dict] = Field(default_factory=list, max_length=45000)

    @field_validator("codes")
    @classmethod
    def valid_codes(cls, codes):
        if any(not re.fullmatch(r"(sh|sz)\.\d{6}", c) for c in codes):
            raise ValueError("代码格式示例：sh.600000")
        return list(dict.fromkeys(codes))


class BacktestRequest(BaseModel):
    strategy_id: str
    strategy_version: str
    strategy_digest: str
    codes: list[str] = Field(min_length=1, max_length=500)
    start: date
    end: date
    benchmark: str = "sh.000300"
    parameters: dict = Field(default_factory=dict)
    capital: float = Field(default=1000000, gt=0, le=1e12)
    commission_rate: float = Field(default=0.0003, ge=0, le=0.02)
    minimum_commission: float = Field(default=5, ge=0, le=10000)
    transfer_rate: float = Field(default=0.00001, ge=0, le=0.01)
    slippage: float = Field(default=0.0005, ge=0, le=0.1)
    participation: float = Field(default=0.05, gt=0, le=1)
    dividend_tax: float = Field(default=0.1, ge=0, le=0.2)
    risk_free: float = Field(default=0.02, ge=0, le=0.2)


class InstallRequest(BaseModel):
    path: str


class ResearchInterval(BaseModel):
    start: date
    end: date


class ExperimentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    config: BacktestRequest
    intervals: dict[Literal["train", "validation", "out_of_sample"], ResearchInterval]
    candidates: list[dict] = Field(min_length=1, max_length=20)


class ResearchChoice(BaseModel):
    candidate: int = Field(ge=0, lt=20)


class SyncRequest(BaseModel):
    enabled: bool = True
    codes: list[str] = Field(default_factory=list, max_length=100)
    periods: list[Literal["w", "m"]] = Field(default_factory=list)
    minutes: list[Literal["5", "15", "30", "60"]] = Field(default_factory=list)

    @field_validator("codes")
    @classmethod
    def valid_codes(cls, value):
        return CollectRequest.valid_codes(value)


@app.get("/api/sync")
def sync_settings():
    return db.one("SELECT config FROM sync_settings WHERE id=1")["config"]


@app.post("/api/sync")
def save_sync(request: SyncRequest):
    db.execute("UPDATE sync_settings SET config=%s WHERE id=1", (db.jb(request.model_dump()),))
    return {"ok": True}


class UniverseRange(BaseModel):
    start: date = date(2023, 1, 1)
    end: date


class HistoryRequest(UniverseRange):
    asset: Literal["stock", "etf"] = "stock"
    pool_mode: Literal["end_day", "historical"] = "end_day"


@app.get("/api/universe/coverage")
def universe_coverage(start: date, end: date, version: int | None = Query(None, ge=0)):
    return universe.coverage(str(start),str(end),db.latest_version() if version is None else version)


@app.get("/api/universe")
def universe_rows(day: date, q: str = "", page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=1000), version: int | None = Query(None, ge=0)):
    return universe.page(str(day),db.latest_version() if version is None else version,q,page,size)


@app.get("/api/universe/export")
def universe_export(day: date, version: int | None = Query(None, ge=0)):
    version = db.latest_version() if version is None else version
    source = universe.snapshot(str(day),version)
    def content():
        yield "\ufeffcode,tradeStatus,code_name\r\n"
        for page in range(1,(len(source["codes"])+999)//1000+1):
            buffer = StringIO()
            writer = csv.writer(buffer)
            for row in universe.page(str(day),version,page=page,size=1000)["data"]:
                values = [str(row[field]) for field in ("code","tradeStatus","code_name")]
                values = ["'"+value if value.startswith(("=","+","-","@")) else value for value in values]
                writer.writerow(values)
            yield buffer.getvalue()
    return StreamingResponse(content(),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="universe-{day}-v{version}.csv"'})


@app.post("/api/universe/backfill")
def backfill_universe(request: UniverseRange):
    status = universe.coverage(str(request.start),str(request.end),db.latest_version())
    operations = [{"method":"query_all_stock","arguments":{"day":day}} for day in status["missing_days"]]
    identity = {"start":str(request.start),"end":str(request.end)}
    config = {"universe_range":identity,"operations":operations,"note":"按交易日采集历史成员和交易状态，名称保留回溯原值；不改用当前证券池"}
    with db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("universe_backfill:"+db.dumps(identity),))
        active = conn.execute("SELECT id FROM jobs WHERE kind='collect' AND status IN ('queued','running','waiting') AND config->'universe_range'=%s ORDER BY created_at LIMIT 1", (db.jb(identity),)).fetchone()
        if active:
            job_id = str(active["id"])
        elif operations:
            job_id = str(uuid4())
            conn.execute("INSERT INTO jobs(id,kind,config) VALUES(%s,'collect',%s)", (job_id,db.jb(config)))
        else:
            return {"job_id":None,"days":0,"note":"区间交易日证券池均已有完整已发布快照"}
    return {"job_id":job_id,"days":len(operations),"note":config["note"]}


@app.post("/api/backfill")
def backfill(request: HistoryRequest):
    if request.start > request.end or request.end > date.today():
        raise ValueError("请检查回填日期，结束日期不得在未来")
    version = db.latest_version()
    days = universe.trading_days(str(request.start),str(request.end),version)
    day = days[-1]
    if request.pool_mode == "historical":
        pool = universe.members(str(request.start),day,version)
    else:
        pool = universe.snapshot(day,version)["codes"]
    types, classification_sources = universe.classify_pool(pool, version)
    asset_type = "1" if request.asset == "stock" else "5"
    codes = sorted({code for code in pool if types.get(code) == asset_type})
    if not codes:
        raise ValueError("当日证券池尚无对应类型的证券，未自动改用当前名单")
    if request.asset == "etf" and request.start < date(2026, 1, 5):
        raise ValueError("BaoStock ETF 历史覆盖从 2026-01-05 起")
    config = {"codes": codes, "start": str(request.start), "end": day, "frequency": "d", "pool_day": day, "pool_version": version, "asset": request.asset,
              "pool_mode":request.pool_mode, "classification_sources":classification_sources,
              "note": "历史逐日证券池成员并集回填，包含区间内后来退出的证券；资产类型按固定版本档案映射" if request.pool_mode == "historical" else "结束日证券池回填，不等于区间内每一天的全市场历史股票池"}
    if classification_sources:
        config["note"] += "；缺档案成员仅采用明确核验的交易所分类：" + "、".join(classification_sources) + "（不补写来源档案/名称/状态）"
    active = db.one("SELECT id FROM jobs WHERE kind='collect' AND status IN ('queued','running','waiting') AND config=%s", (db.jb(config),))
    return {"job_id": str(active["id"]) if active else jobs.submit("collect", config), "codes": len(codes), "note": config["note"], "classification_sources":classification_sources}


@app.get("/api/health")
def health():
    result = db.one("SELECT current_database() AS database, version() AS postgres, EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid=81432002 AND objsubid=1 AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) AND granted) AS worker")
    return {"status": "ok", "database": result["database"], "worker": result["worker"],
            "postgres": result["postgres"].split(",")[0], "version": db.latest_version(),
            "backtest_assets": {"etf_codes": ETF_CODES, "etf_start": ETF_START, "etf_end": ETF_END,
                                "growth_start": "2023-01-01", "growth_end": GROWTH_END}}


@app.get("/api/overview")
def overview():
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        stats = conn.execute("SELECT * FROM dataset_stats ORDER BY dataset").fetchall()
        version = conn.execute("SELECT COALESCE(max(id),0) AS version FROM data_batches WHERE status='published'").fetchone()["version"]
        market_summary = summarize(conn, version)
        counts = conn.execute("SELECT status,count(*) AS count FROM jobs GROUP BY status").fetchall()
        usage = conn.execute("SELECT * FROM request_usage ORDER BY day DESC LIMIT 7").fetchall()
        coverage = conn.execute("""WITH day AS (
          SELECT s.day::text AS day,s.codes FROM universe_snapshots s JOIN data_batches b ON b.id=s.batch_id
          WHERE b.status='published' ORDER BY s.day DESC,s.batch_id DESC LIMIT 1
        ), pool AS (
          SELECT unnest(d.codes) AS code,d.day FROM day d
        ), basic AS (
          SELECT DISTINCT ON(r.code) r.code,r.payload->>'type' AS type FROM source_records r JOIN data_batches b ON b.id=r.batch_id
          WHERE r.dataset='instruments' AND b.status='published' ORDER BY r.code,r.batch_id DESC
        ) SELECT CASE basic.type WHEN '1' THEN 'A 股' ELSE 'ETF' END AS asset,pool.day AS date,count(*) AS expected,
          count(*) FILTER(WHERE q.period::text=pool.day) AS present,
          count(*) FILTER(WHERE q.period::text IS DISTINCT FROM pool.day) AS missing,
          COALESCE((array_agg(pool.code ORDER BY pool.code) FILTER(WHERE q.period::text IS DISTINCT FROM pool.day))[1:10],'{}') AS examples
        FROM pool JOIN basic ON basic.code=pool.code LEFT JOIN latest_quotes q ON q.code=pool.code
        WHERE basic.type IN ('1','5') GROUP BY basic.type,pool.day ORDER BY basic.type""").fetchall()
    return {"datasets": stats, "indices": market_summary["indices"], "market_summary": market_summary,
            "version": version, "market_coverage": coverage,
            "jobs": counts, "usage": usage,
            "coverage_note": "按当日证券池及当前档案类型核对最新日线，不代表全历史完整"}


@app.get("/api/instruments")
def instruments(filters: MarketFilter = Depends(), page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    return search(filters, page, size)


class SavedFilterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    config: MarketFilter


@app.get("/api/filters")
def saved_filters():
    return db.rows("SELECT * FROM saved_filters ORDER BY created_at DESC")


@app.get("/api/filters/{filter_id}/export")
def export_filter(filter_id: UUID):
    saved = db.one("SELECT * FROM saved_filters WHERE id=%s", (filter_id,))
    if not saved:
        raise HTTPException(404, "筛选条件不存在")
    document = {"format": "stock-alei-market-filter", "format_version": 1,
                "source_id": saved["id"], "saved_at": saved["created_at"],
                "name": saved["name"], "config": saved["config"],
                "notes": ["条件作用于当前档案、最新已发布行情、当前行业与自选；各证券行情日期可能不同，不是历史股票池快照。",
                          "PE/PB 为来源比值，成交额下限单位为元；条件为空不设限制，来源缺值不按零参与区间筛选。",
                          "排序缺值置后；非代码排序同值时按代码升序。自选条件引用本机当前自选，不包含成员副本。",
                          "导出保存的条件，不包含当前未保存修改、证券结果、数据版本或回测配置。"]}
    return Response(db.dumps(document), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="market-filter-{filter_id}.json"'})


@app.post("/api/filters")
def save_filter(request: SavedFilterRequest):
    if not request.name.strip():
        raise ValueError("筛选名称不能为空白")
    filter_id = uuid4()
    search(request.config, 1, 1)
    db.execute("INSERT INTO saved_filters(id,name,config) VALUES(%s,%s,%s)", (filter_id, request.name.strip(), db.jb(request.config.model_dump())))
    return {"id": str(filter_id)}


@app.delete("/api/filters/{filter_id}")
def delete_filter(filter_id: UUID):
    db.execute("DELETE FROM saved_filters WHERE id=%s", (filter_id,))
    return {"ok": True}


@app.get("/api/instruments/{code}")
def instrument_detail(code: str):
    data = db.payloads("instruments", code=code)
    if not data:
        raise HTTPException(404, "证券未入库")
    return {"basic": data[-1], "code_change": CODE_CHANGE_REFERENCE if code == CODE_CHANGE_REFERENCE["code"] else None,
            "collection_only_action_note": ZHONGYAN_LIMIT if code == 'sz.003001' else None,
            "industry": db.payloads("industry", code=code),
            "dividends": db.payloads("dividends", code=code), "factors": db.payloads("factors", code=code),
            "official_actions": db.payloads("official_actions", code=code),
            "financial": {name: db.payloads(name, code=code) for name in ("profit", "operation", "growth", "balance", "cash_flow", "dupont", "express", "forecast")}}


@app.get("/api/bars/{code}")
def bars(code: str, start: date = date(2023, 1, 1), end: date | None = None, frequency: str = "d", adjustment: Literal["raw", "forward", "backward"] = "raw", limit: int = Query(5000, ge=1, le=20000), version: int | None = Query(None, ge=0), rvc001_red_exit: Literal["pink", "pink_or_green"] = "pink_or_green"):
    if frequency not in {"d", "w", "m", "5", "15", "30", "60"}:
        raise ValueError("不支持的频率")
    end = end if end is not None else datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return chart(code, frequency, str(start), str(end), adjustment, limit, version, rvc001_red_exit)


@app.post("/api/watchlist/{code}")
def watch_add(code: str):
    if not db.payloads("instruments", code=code):
        raise ValueError("证券未入库")
    db.execute("INSERT INTO watchlist(code) VALUES(%s) ON CONFLICT DO NOTHING", (code,))
    return {"ok": True}


@app.delete("/api/watchlist/{code}")
def watch_remove(code: str):
    db.execute("DELETE FROM watchlist WHERE code=%s", (code,))
    return {"ok": True}


@app.get("/api/catalog")
def api_catalog():
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        evidence = conn.execute("SELECT DISTINCT ON(method) method,status,fields,row_count,published_at FROM data_batches WHERE status='published' ORDER BY method,id DESC").fetchall()
        counts = conn.execute("SELECT method,count(*) AS published_batches,count(*) FILTER(WHERE row_count>0) AS nonempty_batches,sum(row_count) AS received_rows FROM data_batches WHERE status='published' GROUP BY method").fetchall()
        source_stats = conn.execute("SELECT * FROM dataset_stats ORDER BY dataset").fetchall()
        version = conn.execute("SELECT COALESCE(max(id),0) AS version FROM data_batches WHERE status='published'").fetchone()["version"]
    lookup = {r["method"]: r for r in evidence}
    collection = {r["method"]: r for r in counts}
    stats = {r["dataset"]: r for r in source_stats}
    return [{**e, "verification": lookup.get(e["method"]), "snapshot_version": version,
             "support": "采集发布、来源覆盖与历史用途分别核对", "historical_support": macro_policy(e["dataset"]),
             "capabilities": capabilities(e, collection.get(e["method"]), stats)} for e in catalogue()]


@app.get("/api/datasets/{dataset}")
def dataset_rows(dataset: str, code: str | None = None, page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=1000), version: int | None = Query(None, ge=0)):
    if dataset not in {e.dataset for e in BY_METHOD.values()} | REFERENCE_DATASETS | {"bars:d", "bars:w", "bars:m", "bars:5", "bars:15", "bars:30", "bars:60", "official_actions"}:
        raise ValueError("未知数据集")
    version = db.latest_version() if version is None else version
    return {**db.payload_page(dataset, code=code or None, version=version, page=page, size=size),
            "version": version, "page": page, "dataset": dataset,
            "historical_support": describe_macro(dataset, db.payloads(dataset, code=code or None, version=version)) if dataset in MACRO_POLICIES else None}


@app.get("/api/datasets/{dataset}/export")
def dataset_export(dataset: str, code: str | None = None, version: int | None = Query(None, ge=0)):
    if dataset not in {e.dataset for e in BY_METHOD.values()} | REFERENCE_DATASETS | {"bars:d", "bars:w", "bars:m", "bars:5", "bars:15", "bars:30", "bars:60", "official_actions"}:
        raise ValueError("未知数据集")
    version = db.latest_version() if version is None else version
    methods = [e.method for e in BY_METHOD.values() if e.dataset == dataset or (dataset.startswith("bars:") or dataset in REFERENCE_DATASETS) and e.dataset == "bars"]
    if dataset == "official_actions":
        methods.append("verified_official_announcement")
    evidence = db.rows("SELECT fields FROM data_batches WHERE method=ANY(%s) AND status='published' AND id<=%s ORDER BY id", (methods, version))
    fields = list(dict.fromkeys(field for row in evidence for field in row["fields"]))
    def content():
        yield "\ufeff"
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(fields)
        yield buffer.getvalue()
        for row in db.stream_payloads(dataset, code=code or None, version=version):
            buffer.seek(0); buffer.truncate(0)
            cells = []
            for field in fields:
                value = "" if row.get(field) is None else str(row[field])
                if value.startswith(("=", "+", "-", "@")) and not re.fullmatch(r"[+-]?[0-9]+([.][0-9]+)?", value):
                    value = "'" + value
                cells.append(value)
            writer.writerow(cells)
            yield buffer.getvalue()
    return StreamingResponse(content(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{dataset.replace(":","-")}-v{version}.csv"'})


@app.post("/api/collect")
def collect(request: CollectRequest):
    config = request.model_dump(mode="json")
    if request.start > request.end or not request.codes and not request.operations:
        raise ValueError("请提供股票池或接口操作，并检查日期范围")
    config["operations"] = [validate_operation(o) for o in request.operations]
    active = db.one("SELECT id FROM jobs WHERE kind='collect' AND status IN ('queued','running') AND config=%s LIMIT 1", (db.jb(config),))
    return {"job_id": str(active["id"]) if active else jobs.submit("collect", config)}


@app.get("/api/jobs")
def job_list(page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=100), paged: bool = False):
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        records = conn.execute("SELECT id,kind,status,progress,total,error,created_at,started_at,finished_at,heartbeat_at,cancel_requested "
                               "FROM jobs WHERE status IN ('queued','running','waiting') OR id IN "
                               "(SELECT id FROM jobs WHERE status NOT IN ('queued','running','waiting') ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s) "
                               "ORDER BY (status IN ('queued','running','waiting')) DESC,created_at DESC,id DESC", (size, (page-1)*size)).fetchall()
        if not paged:
            return records
        total = conn.execute("SELECT count(*) AS total FROM jobs WHERE status NOT IN ('queued','running','waiting')").fetchone()["total"]
        return {"data": records, "total": total, "page": page, "size": size,
                "active_count": sum(row["status"] in ('queued','running','waiting') for row in records)}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: UUID, log_page: int = Query(1, ge=1), log_size: int = Query(200, ge=1, le=200)):
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        job = conn.execute("SELECT * FROM jobs WHERE id=%s", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "任务不存在")
        job["logs"] = conn.execute("SELECT level,message,created_at FROM job_logs WHERE job_id=%s ORDER BY id DESC LIMIT %s OFFSET %s", (job_id, log_size, (log_page-1)*log_size)).fetchall()
        job["logs_total"] = conn.execute("SELECT count(*) AS total FROM job_logs WHERE job_id=%s", (job_id,)).fetchone()["total"]
        job["log_page"], job["log_size"] = log_page, log_size
        return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: UUID):
    db.execute("UPDATE jobs SET cancel_requested=true,status=CASE WHEN status IN ('queued','waiting') THEN 'cancelled' ELSE status END,finished_at=CASE WHEN status IN ('queued','waiting') THEN now() ELSE finished_at END WHERE id=%s AND status IN ('queued','running','waiting')", (job_id,))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: UUID):
    job = db.one("SELECT * FROM jobs WHERE id=%s", (job_id,))
    if not job or job["status"] not in {"failed", "cancelled", "interrupted"}:
        raise ValueError("仅失败、取消或中断任务可重跑")
    db.execute("UPDATE jobs SET status='queued',cancel_requested=false,error=NULL,finished_at=NULL,retry_after=NULL,checkpoint=checkpoint-'retry_count' WHERE id=%s", (job_id,))
    return {"job_id": str(job_id)}


@app.get("/api/strategies")
def strategies():
    return db.rows("SELECT id,version,digest,manifest,enabled,created_at FROM strategies ORDER BY created_at DESC")


@app.post("/api/strategies/install")
def strategy_install(request: InstallRequest):
    return plugins.install(request.path)


@app.patch("/api/strategies/{strategy_id}/{version}/{digest}")
def strategy_enable(strategy_id: str, version: str, digest: str, enabled: bool):
    db.execute("UPDATE strategies SET enabled=%s WHERE id=%s AND version=%s AND digest=%s", (enabled, strategy_id, version, digest))
    return {"ok": True}


@app.post("/api/backtests")
def create_backtest(request: BacktestRequest):
    return {"job_id": submit_backtest(request.model_dump(mode="json"))}


@app.get("/api/backtests")
def runs(page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=100), paged: bool = False):
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        records = conn.execute("SELECT r.id,r.manifest->'config' AS config,r.manifest->>'data_version' AS data_version,r.result->'metrics' AS metrics,j.status,j.created_at FROM backtest_runs r JOIN jobs j ON j.id=r.id ORDER BY j.created_at DESC,r.id DESC LIMIT %s OFFSET %s", (size, (page-1)*size)).fetchall()
        if not paged:
            return records
        total = conn.execute("SELECT count(*) AS total FROM backtest_runs").fetchone()["total"]
        return {"data": records, "total": total, "page": page, "size": size}


@app.post("/api/experiments")
def create_experiment(request: ExperimentRequest):
    value = request.model_dump(mode="json")
    return {"experiment_id": experiments.create(**value)}


@app.get("/api/experiments")
def list_experiments():
    return db.rows("SELECT id,name,selected,created_at FROM experiments ORDER BY created_at DESC LIMIT 100")


@app.get("/api/experiments/{experiment_id}")
def experiment_detail(experiment_id: UUID):
    return experiments.detail(experiment_id)


@app.post("/api/experiments/{experiment_id}/lock")
def lock_experiment(experiment_id: UUID, request: ResearchChoice):
    return {"job_id": experiments.lock_choice(experiment_id, request.candidate)}


@app.post("/api/backtests/{run_id}/replay")
def replay_backtest(run_id: UUID):
    return {"job_id": replay(run_id)}


@app.get("/api/backtests/{run_id}")
def run_detail(run_id: UUID):
    result = db.one("SELECT r.*,j.status,j.error,j.peak_rss FROM backtest_runs r JOIN jobs j ON j.id=r.id WHERE r.id=%s", (run_id,))
    if not result:
        raise HTTPException(404, "回测不存在")
    result["manifest"]["strategy"].pop("source_path", None)
    return result


@app.get("/api/backtests/{run_id}/export")
def export_run(run_id: UUID):
    return Response(db.dumps(run_detail(run_id)), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="backtest-{run_id}.json"'})


if (ROOT / "frontend" / "dist").exists():
    app.mount("/", StaticFiles(directory=ROOT / "frontend" / "dist", html=True), name="web")

