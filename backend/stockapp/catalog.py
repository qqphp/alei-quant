"""BaoStock 0.9.4 官方数据方法目录；浏览/回测只访问已入库数据。"""
from dataclasses import dataclass, asdict
import inspect
import re
from datetime import datetime
import baostock as bs


@dataclass(frozen=True)
class Endpoint:
    method: str
    dataset: str
    label: str
    document: str
    historical_note: str = "历史覆盖与字段完整度以实际查询为准"


ENDPOINTS = [
    Endpoint("query_history_k_data_plus", "bars", "历史 K 线", "stockKData.md", "指数无分钟线；ETF 从 2026-01-05 起；股票分钟起点需核验"),
    Endpoint("query_daily_history_k_AStock", "bars:d", "全市场 A 股日线", "DailyUpdates.md"),
    Endpoint("query_daily_history_k_ETF", "bars:d", "全市场 ETF 日线", "DailyUpdates.md"),
    Endpoint("query_stock_basic", "instruments", "证券基本资料", "stockBasic.md"),
    Endpoint("query_all_stock", "universe", "历史证券池", "StockBasicInfoAPI.md"),
    Endpoint("query_trade_dates", "calendar", "交易日历", "StockBasicInfoAPI.md"),
    Endpoint("query_stock_industry", "industry", "行业分类", "stockIndustry.md"),
    Endpoint("query_sz50_stocks", "sz50", "上证 50 成分", "sz50Stock.md"),
    Endpoint("query_hs300_stocks", "hs300", "沪深 300 成分", "hs300Stock.md"),
    Endpoint("query_zz500_stocks", "zz500", "中证 500 成分", "zz500Stock.md"),
    Endpoint("query_adjust_factor", "factors", "历史复权因子", "factorInfo.md"),
    Endpoint("query_daily_adjust_factor", "factors", "每日复权因子", "DailyUpdates.md"),
    Endpoint("query_dividend_data", "dividends", "分红送转", "dividInfo.md"),
    Endpoint("query_profit_data", "profit", "盈利能力", "seasonProfit.md"),
    Endpoint("query_operation_data", "operation", "营运能力", "seasonOperation.md"),
    Endpoint("query_growth_data", "growth", "成长能力", "seasonGrowth.md"),
    Endpoint("query_balance_data", "balance", "偿债能力", "seasonBalance.md"),
    Endpoint("query_cash_flow_data", "cash_flow", "现金流", "seasonCashFlow.md"),
    Endpoint("query_dupont_data", "dupont", "杜邦分析", "seasonDupont.md"),
    Endpoint("query_performance_express_report", "express", "业绩快报", "seasonExpress.md"),
    Endpoint("query_forecast_report", "forecast", "业绩预告", "seasonForecast.md"),
    Endpoint("query_deposit_rate_data", "deposit_rate", "存款利率", "depositRate.md"),
    Endpoint("query_loan_rate_data", "loan_rate", "贷款利率", "loanRate.md"),
    Endpoint("query_required_reserve_ratio_data", "reserve_ratio", "存款准备金率", "reserveRatio.md"),
    Endpoint("query_money_supply_data_month", "money_month", "月度货币供应量", "supplyData.md", "缺少发布日期，默认仅供浏览"),
    Endpoint("query_money_supply_data_year", "money_year", "年度货币供应量", "supplyDataYear.md", "缺少发布日期，默认仅供浏览"),
]
BY_METHOD = {e.method: e for e in ENDPOINTS}
DAILY_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"
INDEX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,pctChg"
PERIOD_FIELDS = "date,code,open,high,low,close,volume,amount,adjustflag,turn,pctChg"
MINUTE_FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"
REFERENCE_DATASETS = {"reference:bars:d:1", "reference:bars:d:2"}


def catalogue():
    return [{**asdict(e), "url": "https://www.baostock.com/mainContent?file=" + e.document,
             "signature": str(inspect.signature(getattr(bs, e.method)))} for e in ENDPOINTS]


def validate_operation(operation):
    method, arguments = operation.get("method"), operation.get("arguments", {})
    if not isinstance(method, str) or method not in BY_METHOD:
        raise ValueError("不支持的数据方法")
    try:
        inspect.signature(getattr(bs, method)).bind(**arguments)
    except TypeError as exc:
        raise ValueError(f"{method} 参数不符合官方接口签名：{exc}") from exc
    if method in {"query_money_supply_data_month", "query_money_supply_data_year"}:
        pattern = r"\d{4}-(0[1-9]|1[0-2])" if method.endswith("month") else r"\d{4}"
        for field in ("start_date", "end_date"):
            value = arguments.get(field)
            if value and not re.fullmatch(pattern, str(value)):
                raise ValueError("货币供应量日期格式：月度 YYYY-MM，年度 YYYY")
    if method == "query_all_stock" and not arguments.get("day"):
        raise ValueError("历史证券池需明确指定 day，以保存查询日期")
    if method == "query_history_k_data_plus":
        flag = arguments.get("adjustflag", "3")
        if not isinstance(flag, str) or flag not in {"1", "2", "3"}:
            raise ValueError("复权标记必须为字符串 1、2 或 3")
        if flag != "3" and str(arguments.get("frequency", "d")) != "d":
            raise ValueError("官方复权对照样本仅接受日线，独立保存，不供回测撮合使用")
        fields = arguments.get("fields", "")
        if flag != "3" and (not isinstance(fields, str) or not {"date", "code", "open", "high", "low", "close", "adjustflag"}.issubset(fields.split(","))):
            raise ValueError("官方复权对照样本需包含日期、代码、OHLC 与复权标记")
        if str(arguments.get("frequency", "d")) not in {"d", "w", "m", "5", "15", "30", "60"}:
            raise ValueError("不支持的频率")
    return {"method": method, "arguments": arguments}


def record_identity(endpoint, arguments, row):
    dataset = endpoint.dataset
    period = row.get("date") or row.get("statDate") or row.get("performanceExpStatDate") or row.get("profitForcastExpStatDate") or row.get("pubDate") or row.get("updateDate") or row.get("calendar_date")
    if dataset == "bars":
        dataset = "bars:" + str(arguments.get("frequency", "d"))
        if arguments.get("adjustflag", "3") != "3":
            dataset = "reference:" + dataset + ":" + arguments["adjustflag"]
    if dataset.startswith("bars:") or dataset in REFERENCE_DATASETS:
        timestamp = row.get("time")
        if timestamp:
            if not re.fullmatch(r"\d{17}", timestamp):
                raise ValueError("分钟时间必须为 YYYYMMDDHHMMSSmmm")
            period = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}T{timestamp[8:10]}:{timestamp[10:12]}:{timestamp[12:14]}.{timestamp[14:]}"
            datetime.fromisoformat(period)
            if not period.startswith(row["date"]):
                raise ValueError("分钟日期与时间不一致")
        else:
            period = row["date"]
        parts = [row["code"], period]
    elif dataset == "instruments":
        parts = [row["code"]]
    elif dataset == "universe":
        period = arguments.get("day")
        if not period:
            raise ValueError("证券池采集必须指定日期")
        parts = [period, row["code"]]
    elif dataset == "dividends":
        period = row.get("dividOperateDate") or row.get("dividPlanAnnounceDate")
        parts = [row.get("code"), period, row.get("dividRegistDate"), row.get("dividPlanAnnounceDate"), row.get("dividPlanDate"), row.get("dividAgmPumDate")]
        from .corporate_actions import verified_component, verified_revision
        component = verified_component(row)
        if component:
            parts.append(component)
        revision = verified_revision(row)
        if revision:
            parts.append(revision)
        from .source_revisions import collection_revision
        collected_revision = collection_revision(row)
        if collected_revision:
            parts.append(collected_revision['label'])
    elif dataset == "factors":
        period = row.get("dividOperateDate") or row.get("date")
        parts = [row.get("code"), period]
    elif dataset in {"sz50", "hs300", "zz500", "industry"}:
        parts = [row.get("updateDate"), row.get("code")]
    elif dataset == "calendar":
        parts = [row.get("calendar_date")]
    else:
        if row.get("statYear"):
            period = row["statYear"] + ("-" + row["statMonth"].zfill(2) if row.get("statMonth") else "")
        parts = [row.get(k) for k in ("code", "statDate", "pubDate", "effectiveDate", "moneyType", "performanceExpStatDate", "performanceExpPubDate", "performanceExpUpdateDate", "profitForcastExpStatDate", "profitForcastExpPubDate", "profitForcastType", "statYear", "statMonth") if row.get(k)]
        if not parts:
            raise ValueError(f"{endpoint.label} 没有可识别的自然键，保留失败记录并等待字段适配")
    return dataset, "|".join(str(p or "") for p in parts), row.get("code"), period

