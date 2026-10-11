"""上海时区按源就绪时间补跑；幂等任务键避免重复连接。"""
from datetime import datetime, timedelta, date
import hashlib
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import db
from .catalog import DAILY_FIELDS, INDEX_FIELDS, PERIOD_FIELDS, MINUTE_FIELDS

INDEX_CODES = ("sh.000001", "sh.000300", "sh.000905", "sz.399001")


def enqueue(key, operations):
    if not operations:
        return
    db.execute("INSERT INTO jobs(id,kind,config,schedule_key) VALUES(%s,'collect',%s,%s) ON CONFLICT(schedule_key) WHERE schedule_key IS NOT NULL DO NOTHING",
               (uuid4(), db.jb({"operations": operations}), key))


def enqueue_many(entries):
    if not entries:
        return
    with db.connect() as conn, conn.cursor() as cursor:
        # 实际插入时刻保留排队顺序；单事务不为每个欠缺日重复建立连接。
        cursor.executemany("INSERT INTO jobs(id,kind,config,schedule_key,created_at) VALUES(%s,'collect',%s,%s,clock_timestamp()) "
                           "ON CONFLICT(schedule_key) WHERE schedule_key IS NOT NULL DO NOTHING",
                           [(uuid4(),db.jb({"operations":operations}),key) for key,operations in entries])


def operation(method, **arguments):
    return {"method": method, "arguments": arguments}


def last_date(method):
    row = db.one("SELECT max(arguments->>'date') AS day FROM data_batches WHERE method=%s AND status='published'", (method,))
    return row["day"]


def catchup_days(ready, cutoff):
    # 先补最早欠缺日；取末尾会在水位前移后永久跳过超过 100 天的积压。
    return [day for day in ready if day > cutoff][:100] if cutoff else ready[-1:]


def bar_watermarks(codes, frequencies):
    rows = db.rows(
        "SELECT c.code,f.frequency,latest.day FROM unnest(%s::text[]) c(code) "
        "CROSS JOIN unnest(%s::text[]) f(frequency) LEFT JOIN LATERAL "
        "(SELECT left(r.period,10) AS day FROM source_records r JOIN data_batches b ON b.id=r.batch_id "
        "WHERE r.dataset='bars:'||f.frequency AND r.code=c.code AND b.status='published' "
        "ORDER BY r.period DESC,r.batch_id DESC LIMIT 1) latest ON true", (codes,frequencies))
    return {(r["code"],r["frequency"]):r["day"] for r in rows}


def tick():
    config = db.one("SELECT config FROM sync_settings WHERE id=1")["config"]
    if not config["enabled"]:
        return
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today, clock = now.date(), now.strftime("%H:%M")
    day = today.isoformat()
    enqueue("reference:" + day, [operation("query_stock_basic"), operation("query_trade_dates", start_date=f"{today.year}-01-01", end_date=f"{today.year+1}-12-31")])
    calendar = db.payloads("calendar", end=day)
    trading = [row["calendar_date"] for row in calendar if row["is_trading_day"] == "1"]
    ready = [d for d in trading if d < day or clock >= "17:40"]
    if not ready:
        return
    newest = ready[-1]
    catchup_jobs = []
    # 从各批量接口实际水位补齐错过的交易日，首次启动只采最近就绪日。
    for method in ("query_daily_history_k_AStock", "query_daily_history_k_ETF", "query_daily_adjust_factor"):
        cutoff = last_date(method)
        candidates = ready
        if method == "query_daily_history_k_ETF":
            candidates = [d for d in candidates if d >= "2026-01-05"]
        if method == "query_daily_adjust_factor":
            candidates = [d for d in candidates if d < day or clock >= "18:10"]
        for candidate in catchup_days(candidates,cutoff):
            catchup_jobs.append((f"{method}:{candidate}", [operation(method, date=candidate)]))
    reference_days = {}
    cutoff = db.one("SELECT max(s.day)::text AS day FROM universe_snapshots s JOIN data_batches b ON b.id=s.batch_id WHERE b.status='published'")["day"]
    for candidate in catchup_days(ready,cutoff):
        reference_days.setdefault(candidate,[]).append(operation("query_all_stock",day=candidate))
    index_watermarks = bar_watermarks(list(INDEX_CODES),["d"])
    for code in INDEX_CODES:
        for candidate in catchup_days(ready,index_watermarks[(code,"d")]):
            reference_days.setdefault(candidate,[]).append(operation("query_history_k_data_plus",code=code,fields=INDEX_FIELDS,start_date=candidate,end_date=candidate,frequency="d",adjustflag="3"))
    for candidate in sorted(reference_days)[:100]:
        catchup_jobs.append(("market-history-v2:"+candidate,reference_days[candidate]))
    codes = list(dict.fromkeys(config["codes"] + [r["code"] for r in db.rows("SELECT code FROM watchlist")]))
    scope = hashlib.sha256(db.dumps(sorted(codes)).encode()).hexdigest()[:16]
    basic = {r["code"]: r for r in db.payloads("instruments")}
    stocks = [c for c in codes if basic.get(c, {}).get("type") == "1"]
    if newest < day or clock >= "18:10":
        enqueue(f"company-actions-v2:{newest}:{scope}", [item for code in stocks for item in (
            operation("query_adjust_factor", code=code, start_date=f"{today.year-1}-01-01", end_date=newest),
            operation("query_dividend_data", code=code, year=str(today.year), yearType="operate"))])
    # 次一自然日 01:30 才就绪，使用当天凌晨已完成的最近交易日。
    finance_ready = [d for d in trading if d < day and (d < (today-timedelta(days=1)).isoformat() or clock >= "01:40")]
    if finance_ready:
        finance_day = finance_ready[-1]
        year, month = int(finance_day[:4]), int(finance_day[5:7])
        quarter = (month - 1) // 3 + 1
        if quarter == 1:
            report_year, report_quarter = year-1, 4
        else:
            report_year, report_quarter = year, quarter-1
        # 最近季度可能尚未披露；回看八个已结束季度，兼顾修订和新加入证券的补跑。
        ordinal = report_year*4 + report_quarter - 1
        reports = [divmod(ordinal-offset, 4) for offset in range(8)]
        financial = [operation(method, code=code, year=str(y), quarter=q+1) for code in stocks for y, q in reports for method in (
            "query_profit_data", "query_operation_data", "query_growth_data", "query_balance_data", "query_cash_flow_data", "query_dupont_data")]
        financial += [operation(method, code=code, start_date=f"{year-1}-01-01", end_date=finance_day) for code in stocks for method in ("query_performance_express_report", "query_forecast_report")]
        enqueue(f"financial-v2:{finance_day}:{scope}", financial)
    monday = today - timedelta(days=today.weekday())
    if today.weekday() > 0 or clock >= "18:10":
        enqueue("classification:" + monday.isoformat(), [operation(method, date=monday.isoformat()) for method in (
            "query_stock_industry", "query_sz50_stocks", "query_hs300_stocks", "query_zz500_stocks")])
    saturday = today - timedelta(days=(today.weekday()-5) % 7)
    if today == saturday and clock < "17:40":
        saturday -= timedelta(days=7)
    week_end = (saturday-timedelta(days=1)).isoformat()
    first_of_month = today.replace(day=1)
    if today.day == 1 and clock < "17:40":
        first_of_month = (first_of_month-timedelta(days=1)).replace(day=1)
    month_end = (first_of_month-timedelta(days=1)).isoformat()
    for frequency, through in (("w", week_end), ("m", month_end)):
        if frequency not in config["periods"]:
            continue
        enqueue(f"period-v2:{frequency}:{through}:{scope}", [operation("query_history_k_data_plus", code=code, fields=PERIOD_FIELDS, start_date=(date.fromisoformat(through)-timedelta(days=400)).isoformat(), end_date=through, frequency=frequency, adjustflag="3") for code in codes + list(INDEX_CODES) if basic.get(code, {}).get("type") in {"1","2","5"}])
    minute_ready = [d for d in trading if d < day or clock >= "20:10"]
    if minute_ready and config["minutes"]:
        minute_scope = hashlib.sha256(db.dumps([sorted(codes), sorted(config["minutes"])]).encode()).hexdigest()[:16]
        minute_codes = [code for code in codes if basic.get(code,{}).get("type") in {"1","5"}]
        watermarks = bar_watermarks(minute_codes,config["minutes"])
        minute_days = {}
        for code in minute_codes:
            candidates = [d for d in minute_ready if d >= "2026-01-05"] if basic[code]["type"] == "5" else minute_ready
            for frequency in config["minutes"]:
                for candidate in catchup_days(candidates,watermarks[(code,frequency)]):
                    minute_days.setdefault(candidate,[]).append(operation("query_history_k_data_plus",code=code,fields=MINUTE_FIELDS,start_date=candidate,end_date=candidate,frequency=frequency,adjustflag="3"))
        for candidate in sorted(minute_days)[:100]:
            catchup_jobs.append((f"minute-v3:{candidate}:{minute_scope}",minute_days[candidate]))
    enqueue_many(catchup_jobs)
    enqueue("macro:" + today.strftime("%Y-%m"), [operation(method, start_date=f"{today.year-2}-01-01", end_date=day) for method in (
        "query_deposit_rate_data", "query_loan_rate_data", "query_required_reserve_ratio_data")] + [
        operation("query_money_supply_data_month", start_date=f"{today.year-2}-01", end_date=today.strftime("%Y-%m")),
        operation("query_money_supply_data_year", start_date=str(today.year-2), end_date=str(today.year))])
