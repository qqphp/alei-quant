"""已核验产品的现金二级交易规则；不由 ETF 代码猜测类别。"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from .config import ROOT
from . import db

ETF_START = "2026-01-05"
ETF_END = "2026-10-09"
ETF_CODES = {"sh.510050": "单市场股票（沪）ETF", "sh.510300": "境内跨市场股票 ETF"}
GROWTH_END = "2026-10-09"
BOARD_SOURCES = ["https://star.sse.com.cn/star/en/marketdata/overview/",
                 "https://www.szse.cn/marketServices/technicalservice/doc/P020241212550140892927.pdf"]
ETF_SOURCES = [
    "https://star.sse.com.cn/assortment/fund/list/",
    "https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/10824490/files/dcbe58edb194451d93f19b1f7dd8fb4c.docx",
    "https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml",
    "https://edu.sse.com.cn/best/article/mcontent/c/4731121.shtml",
]
ETF_DIVIDEND = {
    "code": "sh.510300", "dividRegistDate": "2026-01-16", "dividOperateDate": "2026-01-19",
    "dividPayDate": "2026-01-27", "dividCashPsBeforeTax": "0.1230",
    "dividStocksPs": "0", "dividReserveToStockPs": "0", "dividStockMarketDate": "",
    "dividPlanAnnounceDate": "2026-01-12", "announcement_date": "2026-01-12",
    "source": "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-01-12/510300_20260112_VTCZ.pdf",
    "document": "docs/research/510300-202601-dividend.pdf",
    "sha256": "c8ff747d02fa90db72320458c38249f73720b932d61b7e088f033583aab63992",
    "tax_rate": "0", "source_type": "基金管理人正式公告；不是 BaoStock 返回",
}


def rules_for(code, item, start, end):
    if item and item.get("type") == "1" and code.startswith(("sh.60", "sz.00")):
        return {"kind": "stock", "tick": "0.01", "lot": 100, "limit": "0.1", "settlement": "T+1"}
    board = "star" if code.startswith("sh.688") else "gem" if code.startswith("sz.") and "300000" <= code[3:] <= "309799" else None
    if item and item.get("type") == "1" and board:
        if start < "2023-01-01" or end > GROWTH_END:
            raise ValueError(f"{code} 板块规则核验区间为 2023-01-01 至 {GROWTH_END}，需扩展规则后运行")
        return {"kind": "stock", "board": board, "tick": "0.01", "lot": 1 if board == "star" else 100,
                "buy_minimum": 200 if board == "star" else 100, "limit": "0.2", "settlement": "T+1",
                "order_policy": "限价数量规则约束的下一开盘成交近似；未重建真实限价申报、价格笼子或排队",
                "max_order_quantity": 100000 if board == "star" else 300000,
                "market_order_maximum_reference_only": 50000 if board == "star" else 150000,
                "st_daily_buy_maximum": None if board == "star" else 500000,
                "ipo_unlimited_trading_days": 5,
                "effective_rules": [{"start": "2023-01-01", "end": "2023-04-09", "version": "STAR-original" if board == "star" else "GEM-2020+SZSE-2021"},
                                    {"start": "2023-04-10", "end": "2026-07-05", "version": "SSE-2023" if board == "star" else "SZSE-2023"},
                                    {"start": "2026-07-06", "end": GROWTH_END, "version": "SSE-2026" if board == "star" else "SZSE-2026"}],
                "classification_sources": BOARD_SOURCES,
                "coverage": "普通现金账户竞价近似；特殊退市/重新上市事件须另行核验，不能由 isST 或名称推断"}
    if item and item.get("type") == "5" and code in ETF_CODES:
        if start < ETF_START or end > ETF_END:
            raise ValueError(f"{code} 已核验 ETF 来源区间为 {ETF_START} 至 {ETF_END}，需扩展事件覆盖后运行")
        return {"kind": "etf", "category": ETF_CODES[code], "tick": "0.001", "lot": 100,
                "limit": "0.1", "max_order_quantity": 1000000, "settlement": "T+1",
                "stamp_rate": "0", "transfer_rate": "0", "dividend_tax": "0",
                "close_policy": {"change_date": "2026-07-06", "before": "最后一分钟成交量加权日收盘价", "after": "收盘集合竞价"},
                "coverage": {"start": ETF_START, "end": ETF_END,
                    "grade": "正式分红公告与完整原始参考价核对；不保证来源未披露的其它事件"},
                "effective_rules": [{"start": ETF_START, "end": "2026-07-05", "version": "SSE-2023"},
                                    {"start": "2026-07-06", "end": ETF_END, "version": "SSE-2026"}],
                "sources": ETF_SOURCES}
    raise ValueError(f"{code} 对应交易规则尚未核验；支持沪深主板、科创板/创业板 A 股与已核验的 510050/510300 国内股票 ETF")


def growth_documents():
    records = json.loads((ROOT / "docs/research/growth-board-source-files.json").read_text(encoding="utf-8"))
    for record in records:
        raw = (ROOT / "docs/research" / record["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError(f"交易规则原件摘要变化：{record['file']}")
    return records


def floor_quantity(value, rule):
    return int(value / rule["lot"]) * rule["lot"]


def quantity_error(requested, side, held, rule):
    if not rule.get("board"):
        return None
    minimum = rule["buy_minimum"]
    if side == "buy" and (requested < minimum or requested % rule["lot"]):
        return f"买入申报最低 {minimum} 股，增量 {rule['lot']} 股"
    if side == "sell":
        if rule["board"] == "star" and requested < minimum and requested != held:
            return "科创板卖出最低 200 股；余额不足 200 股须一次申报卖出"
        if rule["board"] == "gem" and requested % 100 not in {0, held % 100}:
            return "创业板零股余额须一次申报卖出，不拆分零股"
    if requested > rule["max_order_quantity"]:
        return f"板块单笔限价数量上限 {rule['max_order_quantity']} 股，未模拟拆单"
    return None


def ipo_unlimited_days(item, calendar_rows, start, end):
    ipo = item.get("ipoDate")
    if not ipo:
        raise ValueError("缺少上市日期，不能确定 IPO 前五个交易日")
    days = sorted(r["calendar_date"] for r in calendar_rows if r["is_trading_day"] == "1" and ipo <= r["calendar_date"] <= end)
    if len(days) >= 6 and days[5] < start and days[0] != ipo:
        return set()  # 已知至少六个上市后交易日，不必恢复更早日历来证明成熟上市。
    boundary = days[5] if len(days) >= 6 else end
    known = {r["calendar_date"] for r in calendar_rows}
    day = date.fromisoformat(ipo)
    while day.isoformat() <= boundary:
        if day.isoformat() not in known:
            raise ValueError(f"IPO 交易日计数缺少日历 {day}，未使用行情行号或自然日代替")
        day += timedelta(days=1)
    if not days or days[0] != ipo:
        raise ValueError("证券上市日期不是完整日历中的交易日，需核验正式上市信息")
    return set(days[:5])


def price_bounds(reference, rule, unlimited=False):
    if unlimited:
        return None, None
    tick = Decimal(rule["tick"])
    ratio = Decimal(rule["limit"])
    upper = max(reference + tick, (reference * (1 + ratio)).quantize(tick, rounding=ROUND_HALF_UP))
    lower = max(tick, min(reference - tick, (reference * (1 - ratio)).quantize(tick, rounding=ROUND_HALF_UP)))
    return lower, upper


def import_announcement():
    """把已人工核验的正式原件条款发布到本机修订库；不访问其它网络源。"""
    raw = (ROOT / ETF_DIVIDEND["document"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ETF_DIVIDEND["sha256"]:
        raise ValueError("ETF 正式分红公告原件摘要变化")
    digest = hashlib.sha256(db.dumps(ETF_DIVIDEND).encode()).hexdigest()
    with db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(81432003)")
        old = conn.execute("SELECT r.batch_id,r.content_hash FROM source_records r JOIN data_batches b ON b.id=r.batch_id WHERE r.dataset='official_actions' AND r.record_key=%s AND b.status='published' ORDER BY r.batch_id DESC LIMIT 1", ("sh.510300|2026-01-19",)).fetchone()
        if old and old["content_hash"] == digest:
            return old["batch_id"]
        batch = conn.execute("INSERT INTO data_batches(method,arguments,fields,row_count,changed_count,coverage,status,published_at) VALUES('verified_official_announcement',%s,%s,1,1,%s,'published',now()) RETURNING id",
            (db.jb({"source": ETF_DIVIDEND["source"]}), db.jb(list(ETF_DIVIDEND)),
             db.jb({"source_type": ETF_DIVIDEND["source_type"], "sha256": ETF_DIVIDEND["sha256"], "scope": "仅本次正式公告，非全部公司行动覆盖"}))).fetchone()["id"]
        conn.execute("INSERT INTO source_records(dataset,record_key,code,period,payload,content_hash,batch_id) VALUES('official_actions','sh.510300|2026-01-19','sh.510300','2026-01-19',%s,%s,%s)", (db.jb(ETF_DIVIDEND), digest, batch))
        conn.execute("INSERT INTO dataset_stats(dataset,revisions,latest) VALUES('official_actions',1,'2026-01-19') ON CONFLICT(dataset) DO UPDATE SET revisions=dataset_stats.revisions+1,latest=GREATEST(dataset_stats.latest,excluded.latest)")
        return batch


def supplemental_actions(codes, version):
    if "sh.510300" not in codes:
        return []
    raw = (ROOT / ETF_DIVIDEND["document"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ETF_DIVIDEND["sha256"]:
        raise ValueError("ETF 正式分红公告原件摘要变化，不能固化权益输入")
    rows = db.payloads("official_actions", code="sh.510300", version=version)
    if len(rows) != 1 or rows[0] != ETF_DIVIDEND:
        raise ValueError("指定版本缺少已核验完整 ETF 公告权益，请先导入正式公告条款")
    return deepcopy(rows)
