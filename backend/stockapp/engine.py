"""日线现金账本适配器；已结束行情发信号，下一开盘成交，历史输入固定。"""
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
import random
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np

from . import db, jobs, plugins
from .market import price_series, ready_at
from .period_signals import PeriodSignals
from .minute_bars import MinuteBars, FREQUENCIES as MINUTE_FREQUENCIES
from .fundamentals import load_financial, visible_financial, availability
from .universe_input import HistoricalUniverse, NOTE as UNIVERSE_NOTE
from .corporate_actions import merge_dividends, reference_cash, VERIFIED_REFERENCES, verify_reference_documents
from .asset_rules import rules_for, supplemental_actions, growth_documents, floor_quantity, quantity_error, ipo_unlimited_days, price_bounds
from .config import ROOT

ENGINE_VERSION = "cash-ledger-0.13.1"
D = lambda value: Decimal(str(value))


def cents(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fee(notional, side, day, config, rule=None):
    commission = cents(max(D(config["minimum_commission"]), notional * D(config["commission_rate"])))
    etf = rule and rule["kind"] == "etf"
    stamp = cents(notional * (D("0.0005") if day >= "2023-08-28" else D("0.001"))) if side == "sell" and not etf else D(0)
    transfer = cents(notional * D(0 if etf else config["transfer_rate"]))
    return {"commission": commission, "stamp": stamp, "transfer": transfer, "total": commission + stamp + transfer}


def manifest(config):
    if config["start"] < "2023-01-01":
        raise ValueError("首版历史交易规则从 2023 年起，较早区间须扩充规则后开放")
    if config["start"] >= config["end"]:
        raise ValueError("结束日期必须晚于开始日期")
    strategy = db.one("SELECT * FROM strategies WHERE id=%s AND version=%s AND digest=%s AND enabled",
                      (config["strategy_id"], config["strategy_version"], config["strategy_digest"]))
    if not strategy:
        raise ValueError("模型版本不存在或已停用")
    plugins.validate_manifest(strategy["manifest"])
    config = dict(config)
    config["parameters"] = plugins.validate_parameters(strategy["manifest"], config.get("parameters", {}))
    codes = list(dict.fromkeys(config["codes"]))
    if not codes:
        raise ValueError("至少选择一只证券")
    config["codes"] = codes
    minute = strategy["manifest"]["frequency"] in MINUTE_FREQUENCIES
    version = db.latest_version()
    instruments = {r["code"]: r for r in db.payloads("instruments", version=version)}
    asset_rules = {code: rules_for(code, instruments.get(code), config["start"], config["end"]) for code in codes}
    etf = any(rule["kind"] == "etf" for rule in asset_rules.values())
    growth = any(rule.get("board") for rule in asset_rules.values())
    verify_reference_documents(codes)
    dependencies = dict(sorted((distribution.metadata["Name"], distribution.version) for distribution in importlib.metadata.distributions()))
    return {"config": config, "data_version": version, "strategy": strategy,
            "engine_version": ENGINE_VERSION, "dependencies": dependencies,
            "python_version": sys.version.split()[0],
            "as_of": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "resource_limits": {"memory_bytes": 2*1024**3, "timeout_seconds": 3600},
            "rules_version": "mainboard-2023-v2" + ("+domestic-etf-2026-v1" if etf else "") + ("+growth-2023-2026-v1" if growth else ""),
            "asset_rules": asset_rules, "supplemental_actions": supplemental_actions(codes, version),
            "trading_rule_documents": growth_documents() if growth else [],
            "company_action_references": VERIFIED_REFERENCES, "seed": 0,
            "assumptions": ["现金做多、T+1；主板/创业板百股买入，科创板最低 200 股、增量 1 股；ETF 百份买入、每笔最多 100 万份；不融资、不借券、不模拟一级申赎",
                "历史分钟 bar 结束后决策，下一 bar 开盘执行，零时延成交近似；午休/隔夜顺延到下一常规竞价 bar，目标变化才下单、订单只对下一 bar 有效" if minute else "日线收盘后、周线周六 17:30、月线次月 1 日 17:30 产生对应周期信号；目标变化才下单，订单只在下一可用交易日有效",
                "分钟上下文按当前已结束 bar 估值；财务只有日期，公告日后的首个交易日才可用" if minute else "周/月信号以最近已结束日线估值；财务截至该已结束交易日按披露日期过滤，不提前使用下一交易日才可用的公告",
                "开盘只读取开盘价/前收盘参考/已知交易状态；涨停开盘不买、跌停开盘不卖，属于保守近似",
                "成交量上限使用前一根已完成分钟 bar 的量；价格边界取当天开市已知前收盘基准，不读取执行 bar 的未来高低/收盘/成交量" if minute else "成交量上限使用前一交易日成交量；不使用执行日的未来全天量价",
                "佣金含经手/监管等券商收费；股票另列印花税/过户费与固定股息税，已核验 ETF 二级交易无印花税/过户费、公告现金分红税率为零" if etf else "佣金含用户选择的经手/监管等券商收费，另列卖方印花税和过户费；分红使用用户固定税率假设",
                "财报为按公告/快报更新日期过滤的当前回溯版本，日期次一交易日可用；缺少声明的数据/字段则停止",
                *([UNIVERSE_NOTE] if 'universe' in strategy['manifest'].get('datasets',[]) else []),
                "分红源记录保留方案阶段；相容条款合并，只有逐字段匹配正式更正公告的已核验事件择更正版一次记账，未知经济条款冲突停止",
                "送转除权日开盘前，跨日待执行目标按已知原始开盘价及含权益的开盘净资产重算数量；数量按各资产规则，零股不得拆分申报；这属于平台目标执行政策，不模拟券商隔夜委托自动调整",
                "已核验的差异化分红区分实收股利与摊薄除息金额，公告来源与条款固定在清单；未知特殊行动继续停止",
                "主板风险警示/上市初期继续拒绝；科创/创业普通及 ST 为 20%，IPO 前五个完整日历交易日无涨跌幅；未知特殊事件停止核验，不从名称推断退市整理",
                "科创/创业采用限价数量上限约束的开盘成交近似，未模拟价格笼子、保护限价、临停与盘口排队；不称为真实市价申报；未平仓按期末价格估值，不伪造卖出"],
            "sources": ["https://www.baostock.com/mainContent?file=dividInfo.md",
                        "https://fgk.chinatax.gov.cn/zcfgk/c102416/c5211343/content.html",
                        "https://one.sse.com.cn/onething/gptz/",
                        "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml",
                        "https://investor.szse.cn/institute/rules/t20230629_601434.html"]}


def freeze(config):
    frozen = manifest(config)
    source = ROOT / "backend" / "stockapp"
    digest = plugins.digest_directory(source)
    archived = ROOT / "runtime" / "engines" / digest / "stockapp"
    plugins.archive_directory(source, archived, digest)
    frozen["engine_source"] = {"digest": digest, "package": str(archived.parent)}
    return frozen


def persist_run(conn, frozen):
    job_id = uuid4()
    conn.execute("INSERT INTO jobs(id,kind,config) VALUES(%s,'backtest',%s)", (job_id, db.jb(frozen["config"])))
    conn.execute("INSERT INTO backtest_runs(id,manifest) VALUES(%s,%s)", (job_id, db.jb(frozen)))
    return str(job_id)


def submit_backtest(config):
    frozen = freeze(config)
    with db.connect() as conn:
        return persist_run(conn, frozen)


def replay(run_id):
    previous = db.one("SELECT manifest FROM backtest_runs WHERE id=%s", (run_id,))
    if not previous or not previous["manifest"].get("engine_source"):
        raise ValueError("该开发期运行没有完整引擎归档，请重新提交一次回测")
    frozen = previous["manifest"]
    with db.connect() as conn:
        return persist_run(conn, frozen)


def metrics(equity, trades, initial, risk_free):
    values = np.array([float(e["nav"]) for e in equity], dtype=float)
    sampled = np.concatenate(([float(initial)], values))
    returns = sampled[1:] / sampled[:-1] - 1
    days = len(values)
    years = days / 252
    total = float(values[-1] / float(initial) - 1)
    annual = float((values[-1] / float(initial)) ** (1 / years) - 1) if years > 0 else None
    volatility = float(np.std(returns, ddof=1) * np.sqrt(252)) if days > 1 else None
    sharpe = float((np.mean(returns) - risk_free / 252) / np.std(returns, ddof=1) * np.sqrt(252)) if days > 1 and np.std(returns, ddof=1) > 0 else None
    peak = np.maximum.accumulate(sampled)
    drawdowns = sampled / peak - 1
    benchmark_return = float(equity[-1]["benchmark"] - 1)
    undefined = []
    if not trades:
        undefined.append("区间内没有成交；净值仍包括持仓估值和现金权益，不能据此解释为交易策略有效")
    if volatility is None:
        undefined.append("交易日样本少于两个，波动率和 Sharpe 未定义")
    elif sharpe is None:
        undefined.append("日收益样本标准差为零，Sharpe 未定义")
    return {"total_return": total, "annual_return": annual, "max_drawdown": float(-np.min(drawdowns)),
            "benchmark_return": benchmark_return, "excess_return": total-benchmark_return,
            "excess_method": "策略区间总收益减价格指数区间总收益；百分点差，不是相对净值比或 alpha",
            "notes": undefined,
            "volatility": volatility, "sharpe": sharpe, "trade_count": len(trades),
            "fees": sum(float(t["fees"]["total"]) for t in trades),
            "turnover": sum(float(t["notional"]) for t in trades) / float(np.mean(values)),
            "risk_free": risk_free, "annualization": "252 个交易日；日末净值、样本标准差；换手=双向成交额/平均净资产"}


def backtest(job):
    run = db.one("SELECT manifest FROM backtest_runs WHERE id=%s", (job["id"],))["manifest"]
    cfg, version = run["config"], run["data_version"]
    if run.get("python_version", sys.version.split()[0]) != sys.version.split()[0]:
        raise ValueError("Python 版本变化，请重建原版本环境后复现")
    for dependency, expected in run["dependencies"].items():
        if importlib.metadata.version(dependency) != expected:
            raise ValueError(f"{dependency} 版本变化，请按历史依赖锁重建环境后复现")
    codes, start, end = cfg["codes"], cfg["start"], cfg["end"]
    frequency = run["strategy"]["manifest"]["frequency"]
    as_of = datetime.fromisoformat(run["as_of"])
    random.seed(run["seed"])
    np.random.seed(run["seed"])
    rng = np.random.default_rng(run["seed"])
    strategy_class = plugins.load(run["strategy"])
    strategy = strategy_class(cfg["parameters"])
    financial = load_financial(run["strategy"]["manifest"], codes, version)
    calendar_rows = db.payloads("calendar", end=end, version=version)
    calendar = [r["calendar_date"] for r in calendar_rows if r["is_trading_day"] == "1" and start <= r["calendar_date"]]
    if not calendar:
        raise ValueError("交易日历尚未采集")
    if any(ready_at(day, "d") > as_of for day in calendar):
        raise ValueError("请求区间包含提交时尚未就绪的日线，请将结束日改为已完成交易日")
    prior_calendar = [r["calendar_date"] for r in calendar_rows
                      if r["is_trading_day"] == "1" and r["calendar_date"] < start]
    lookback = run["strategy"]["manifest"]["lookback"]
    daily_lookback = lookback if frequency == "d" else 1
    if len(prior_calendar) < daily_lookback:
        raise ValueError("交易日历不覆盖完整预热窗口，请采集更早日历")
    universe_input = HistoricalUniverse(calendar if frequency == 'd' else [prior_calendar[-1]]+calendar, version) if 'universe' in run['strategy']['manifest'].get('datasets',[]) else None
    bars, histories, dividends = {}, {}, {}
    basic = {r["code"]: r for r in db.payloads("instruments", version=version)}
    asset_rules = run.get("asset_rules") or {c: rules_for(c, basic.get(c), start, end) for c in codes}
    ipo_days = {c: ipo_unlimited_days(basic[c], calendar_rows, start, end) for c in codes if asset_rules[c].get("board")}
    for code in codes:
        series = price_series(code, version=version, end=end)
        bars[code] = {b["date"]: b for b in series}
        histories[code] = [b for b in series if b["date"] < start][-daily_lookback:]
        if len(histories[code]) < daily_lookback:
            raise ValueError(f"{code} 的预热不足，请采集更早数据或缩短预热声明")
        if [b["date"] for b in histories[code]] != prior_calendar[-daily_lookback:]:
            raise ValueError(f"{code} 预热窗口有交易日缺口，未跳过缺失日期计算指标")
        missing = [day for day in calendar if day not in bars[code]]
        if missing:
            raise ValueError(f"{code} 缺少 {len(missing)} 个交易日行情，例如 {missing[0]}，未伪造价格")
        if any(not b.get("preclose") or not math.isfinite(b["preclose"]) or b["preclose"] <= 0
               for b in histories[code] + [bars[code][day] for day in calendar]):
            raise ValueError(f"{code} 的预热/交易窗口缺少有效前收盘基准，无法可靠计算连续价格与交易边界")
        events = db.payloads("dividends", code=code, version=version)
        events += [event for event in run.get("supplemental_actions", []) if event["code"] == code]
        if asset_rules[code]["kind"] == "etf":
            tick = D(asset_rules[code]["tick"])
            actions = merge_dividends(events)
            required_days = {b["date"] for b in histories[code]} | set(calendar)
            for b in histories[code] + [bars[code][day] for day in calendar]:
                if any(D(b[field]).quantize(tick, rounding=ROUND_HALF_UP) != D(b[field]) for field in ("open", "high", "low", "close", "preclose")):
                    raise ValueError(f"{code} {b['date']} ETF 报价不符合 {tick} 元档位")
            # 数据覆盖校验包括无持仓和预热日期，不能因当日空仓漏掉未知行动。
            for prior, current in zip(series, series[1:]):
                if current["date"] not in required_days:
                    continue
                on_day = [a for a in actions if a.get("dividOperateDate") == current["date"]]
                changed = abs(D(prior["close"])-D(current["preclose"])) > tick/2
                if changed or on_day:
                    if len(on_day) != 1 or on_day[0].get("source_type") != "基金管理人正式公告；不是 BaoStock 返回":
                        raise ValueError(f"{code} {current['date']} ETF 公司行动缺少已核验完整公告，停止报告")
                    action = on_day[0]
                    if D(action.get("dividStocksPs") or 0) or D(action.get("dividReserveToStockPs") or 0):
                        raise ValueError(f"{code} ETF 份额折算尚未核验")
                    expected = (D(prior["close"])-D(action["dividCashPsBeforeTax"])).quantize(tick, rounding=ROUND_HALF_UP)
                    if expected != D(current["preclose"]):
                        raise ValueError(f"{code} {current['date']} ETF 除息参考价与正式公告不符")
        if asset_rules[code].get("board"):
            rule = asset_rules[code]
            required_bars = histories[code] + [bars[code][day] for day in calendar]
            required_days = {bar["date"] for bar in required_bars}
            actions = merge_dividends([event for event in events if event.get("dividOperateDate") in required_days])
            for bar in required_bars:
                if bar.get("isST") not in {"0", "1"} or bar.get("tradestatus") not in {"0", "1"}:
                    raise ValueError(f"{code} {bar['date']} 缺少有效风险警示/交易状态，不能套用数量规则")
                if any(D(bar[field]).quantize(D(rule["tick"]), rounding=ROUND_HALF_UP) != D(bar[field]) for field in ("open", "high", "low", "close", "preclose")):
                    raise ValueError(f"{code} {bar['date']} 报价不符合 0.01 元档位")
                lower, upper = price_bounds(D(bar["preclose"]), rule, bar["date"] in ipo_days[code])
                if lower is not None and (D(bar["low"]) < lower or D(bar["high"]) > upper):
                    raise ValueError(f"{code} {bar['date']} 原始范围超出普通/ST 20% 规则，需核验特殊事件而非猜测放开涨跌幅")
            for prior, current in zip(series, series[1:]):
                if current["date"] not in required_days:
                    continue
                on_day = [action for action in actions if action.get("dividOperateDate") == current["date"]]
                if abs(D(prior["close"])-D(current["preclose"])) > D(rule["tick"])/2 or on_day:
                    if len(on_day) != 1:
                        raise ValueError(f"{code} {current['date']} 参考价变化缺少唯一公司行动，即使空仓/预热也停止")
                    action = on_day[0]
                    ex_cash, _ = reference_cash(action)
                    expected = ((D(prior["close"])-ex_cash)/(1+D(action.get("dividStocksPs") or 0)+D(action.get("dividReserveToStockPs") or 0))).quantize(D(rule["tick"]), rounding=ROUND_HALF_UP)
                    if expected != D(current["preclose"]):
                        raise ValueError(f"{code} {current['date']} 参考价与已核验现金/送转条款不符")
        events = [event for event in events if any(start <= (event.get(field) or "") <= end for field in ("dividRegistDate", "dividOperateDate"))]
        dividends[code] = merge_dividends(events)
    period_clock = PeriodSignals(codes, frequency, lookback, start, end, version, run["as_of"],
                                 prior_calendar+calendar, bars) if frequency in {"w", "m"} else None
    minute_clock = MinuteBars(codes, frequency, lookback, start, end, version, as_of,
                              prior_calendar+calendar, bars, asset_rules) if frequency in MINUTE_FREQUENCIES else None
    benchmark = price_series(cfg["benchmark"], version=version, end=end)
    benchmark_by_day = {b["date"]: b for b in benchmark}
    if any(day not in benchmark_by_day for day in calendar):
        raise ValueError("基准行情不覆盖完整区间")
    prior_benchmark = [b for b in benchmark if b["date"] < calendar[0]]
    if not prior_benchmark:
        raise ValueError("基准缺少起始日前的净值基准")
    if prior_benchmark[-1]["date"] != prior_calendar[-1]:
        raise ValueError("基准缺少起始日前最后一个交易日行情，未将净值起点退到更早日期")
    benchmark_base = prior_benchmark[-1]["close"]
    cash, initial = D(cfg["capital"]), D(cfg["capital"])
    lots = {c: [] for c in codes}
    claims, orders, trades, equity, cashflows = {}, [], [], [], []
    intraday_equity = []
    pending, targets = [], {c: 0.0 for c in codes}
    warnings = set()
    if ipo_days:
        warnings.add("科创/创业板以限价数量上限约束下一开盘成交近似，不重建市价类型、连续竞价价格笼子、IPO 临停或集合竞价排队。最低申报量与合法委托的部分成交分开；科创部分成交可少于 200 股，创业部分成交保守按整百处理、零股全额清理。")
        warnings.add("两板普通/ST 按 20%，IPO 前五交易日由完整上市日历计数；未知退市整理/重新上市事件不由名称或 isST 推断。深市 ST 每日累计限额仅统计本模拟账户，不代表真实投资者多账户合并核验。")
    if any(rule["kind"] == "etf" for rule in asset_rules.values()):
        warnings.add("ETF 按已核验产品和来源区间执行，正式管理人公告补充 BaoStock 缺失权益；条款、原件摘要和来源固化在运行清单。原始参考价核对不能保证来源未披露的其它事件完整。")
        warnings.add("ETF 佣金为含规费的客户综合佣金；配置的股票过户费率与固定股息税不用于 ETF。价格保留 0.001 元，现金/费用按分币处理。")
    if period_clock and period_clock.amount_differences:
        warnings.add(f"官方周期与日线汇总有 {len(period_clock.amount_differences)} 项成交额小差异（每项不超过平台核对容差 1 元）；保留官方原值，差异明细随报告导出")
    if minute_clock:
        warnings.add("分钟为历史市场时钟模拟；BaoStock 盘后发布，不能用于实时分钟信号。只模拟常规竞价 bar，未重建集合竞价排队、盘后交易及 bar 内价格路径。")
        warnings.add("分钟最大回撤使用各 bar 收盘净值；基准、波动率、Sharpe 和年化仍按日末净值。停牌缺 bar 时窗口保留最后真实行情，盘中估值采用开市已知前收盘参考。")
        if minute_clock.amount_differences:
            warnings.add(f"分钟与日线汇总有 {len(minute_clock.amount_differences)} 项成交额小差异（容差 1 元），保留官方原值并导出差异。")
        if minute_clock.range_differences:
            warnings.add(f"来源分钟极值未覆盖日线极值，共 {len(minute_clock.range_differences)} 项差异；原因未获来源确认，不改写分钟价格。按来源分钟开收盘运行，不能用于真实盘中触价或止损成交验证，差异明细随报告导出。")
        if minute_clock.close_differences:
            warnings.add(f"ETF 旧收盘规则下有 {len(minute_clock.close_differences)} 项分钟末价/官方日收盘差异；日收盘位于末 bar 范围内，保留两种原值。分钟净值用分钟价、日末风险指标用官方日价，不能由 OHLC 重建末一分钟加权成交；差异逐项导出。")
    signal_log = []
    financial_reports = {}
    universe_references = {}
    if universe_input:
        warnings.add(UNIVERSE_NOTE)

    def quantity(code):
        return sum(l["quantity"] for l in lots[code])

    def receivable():
        return sum((c["cash"] for c in claims.values() if c.get("ex_applied") and not c.get("paid")), D(0))

    def account(prices):
        market_value = sum((D(prices[c]) * quantity(c) for c in codes), D(0))
        return cash + market_value + receivable()

    def decide(at, valuation_day, signal_history, period=None, prices=None, frame=None):
        nonlocal pending, targets
        prices = prices if prices is not None else {c: bars[c][valuation_day]["close"] for c in codes}
        nav = account(prices)
        sellable = {c: sum(l["quantity"] for l in lots[c] if l["bought"] < valuation_day and l["release"] <= valuation_day) for c in codes}
        financial_data = visible_financial(financial, run["strategy"]["manifest"], valuation_day)
        financial_refs = []
        for dataset, by_code in financial_data.items():
            for code, records in by_code.items():
                for row in records:
                    canonical = json.dumps({"dataset": dataset, "payload": row}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    digest = hashlib.sha256(canonical.encode()).hexdigest()
                    financial_refs.append(digest)
                    if digest not in financial_reports:
                        after, report_period = availability(dataset, row)
                        financial_reports[digest] = {"dataset": dataset, "code": code, "period": report_period,
                            "published": row.get("pubDate") or row.get("performanceExpPubDate") or row.get("profitForcastExpPubDate"),
                            "updated": row.get("performanceExpUpdateDate") or None, "available_after": after,
                            "digest": digest, "payload": deepcopy(row)}
        context = {"date": at.date().isoformat(), "signal_at": at.isoformat(), "valuation_date": valuation_day,
                   "frequency": frequency, "codes": list(codes), "history": deepcopy(signal_history), "seed": run["seed"], "rng": rng,
                   "account": {"cash": float(cash), "frozen_cash": 0.0, "receivable": float(receivable()), "nav": float(nav),
                               "positions": {c: quantity(c) for c in codes}, "sellable": sellable},
                   "targets": dict(targets), "first_signal": not signal_log,
                   "data": financial_data}
        if minute_clock:
            context.update(valuation_at=at.isoformat(), bar_present={c: frame[c] is not None for c in codes})
        universe_ref = None
        if universe_input:
            visible_pool = universe_input.visible(at)
            universe_ref = visible_pool['source_batch']
            universe_references[universe_ref] = {key:value for key,value in visible_pool.items() if key != 'data'}
            context['universe'] = visible_pool
        desired = strategy.on_bar(context)
        if not isinstance(desired, dict) or set(desired) - set(codes):
            raise ValueError("策略必须返回股票池内代码到目标权重的字典")
        weights = {c: float(desired.get(c, 0)) for c in codes}
        if any(not math.isfinite(w) or w < 0 or w > 1 for w in weights.values()) or sum(weights.values()) > 1.00000001:
            raise ValueError("做多现金账户权重必须有限、非负且合计不超过 1")
        for code, weight in weights.items():
            if abs(weight - targets[code]) < 1e-10:
                continue
            replaced = [order for order in pending if order["code"] == code]
            pending = [order for order in pending if order["code"] != code]
            orders.extend({**order, "status": "cancelled", "filled": 0, "execution_date": None, "reason": "被开盘前的后续目标替换"} for order in replaced)
            current = quantity(code)
            wanted = floor_quantity(nav * D(weight) / D(prices[code]), asset_rules[code])
            difference = wanted - current
            if difference > 0:
                difference = floor_quantity(D(difference), asset_rules[code])
            if difference:
                order = {"code": code, "side": "buy" if difference > 0 else "sell", "quantity": abs(difference), "signal_date": at.date().isoformat()}
                if frequency != "d":
                    order.update(signal_at=at.isoformat(), signal_period=period)
                pending.append(order)
        targets = weights
        event = {"at": at.isoformat(), "valuation_date": valuation_day, "period": period or valuation_day}
        if financial_refs:
            event["financial_refs"] = financial_refs
        if universe_ref is not None:
            event['universe_ref'] = universe_ref
        signal_log.append(event)

    def execute(open_bars, previous_bars, day, at=None):
        nonlocal cash, pending
        # 策略输出目标权重；送转后的股份单位改变，旧股数不能直接代表原清仓/减仓目标。
        opening_nav = None
        for order in list(pending):
            code = order["code"]
            action = next((event for event in dividends[code] if event.get("dividOperateDate") == day
                           and D(event.get("dividStocksPs") or 0)+D(event.get("dividReserveToStockPs") or 0) > 0), None)
            bar = open_bars.get(code)
            if not action or order["signal_date"] >= day or not bar or not bar["open"] or bar.get("tradestatus") == "0":
                continue
            if opening_nav is None:
                opening_nav = account({c: open_bars[c]["open"] if open_bars.get(c) and open_bars[c]["open"]
                                       else bars[c][day]["preclose"] for c in codes})
            original = {"side": order["side"], "quantity": order["quantity"]}
            wanted = floor_quantity(opening_nav * D(targets[code]) / D(bar["open"]), asset_rules[code])
            difference = wanted-quantity(code)
            if difference > 0:
                difference = floor_quantity(D(difference), asset_rules[code])
            order["corporate_action_adjustment"] = {"date": day, "original": original,
                "target_weight": targets[code], "opening_nav": cents(opening_nav), "opening_price": D(bar["open"]),
                "positions_after_entitlement": quantity(code), "quantity": abs(difference)}
            order.update(quantity=abs(difference))
            if difference:
                order["side"] = "buy" if difference > 0 else "sell"
            warnings.add(f"{code} {day} 送转除权：跨日目标订单按已知开盘价格及含权益净资产重算，原数量和调整依据随订单导出")
            if not difference:
                order.update(status="cancelled", filled=0, execution_date=day, reason="送转除权重算后无需交易")
                if at:
                    order["execution_at"] = at.isoformat()
                orders.append(order)
                pending.remove(order)
        for order in sorted(pending, key=lambda o: (o["side"] != "sell", o["code"])):
            code, side, requested = order["code"], order["side"], order["quantity"]
            rule = asset_rules[code]
            etf = rule["kind"] == "etf"
            tick = D(rule["tick"])
            round_price = lambda value: value.quantize(tick, rounding=ROUND_HALF_UP)
            bar, previous = open_bars.get(code), previous_bars[code]
            if at:
                order["execution_at"] = at.isoformat()
            if bar is None:
                order.update(execution_date=day, filled=0, status="rejected", reason="当前周期无交易行情（停牌），未伪造价格")
                orders.append(order)
                continue
            op = D(bar["open"])
            reference = D(bar["preclose"] or previous["close"])
            lower, upper = price_bounds(reference, rule, day in ipo_days.get(code, set()))
            declaration_error = quantity_error(requested, side, quantity(code), rule)
            reason = None
            if bar.get("tradestatus") == "0":
                reason = "停牌"
            elif not etf and not rule.get("board") and bar.get("isST") == "1":
                reason = "风险警示规则待核验，未模拟成交"
            elif not rule.get("board") and basic[code].get("ipoDate") and (date.fromisoformat(day) - date.fromisoformat(basic[code]["ipoDate"])).days < 120:
                reason = "上市初期规则未核验，未模拟成交"
            elif etf and requested > rule["max_order_quantity"]:
                reason = "ETF 单笔申报超过 100 万份，未模拟拆单"
            elif declaration_error:
                reason = declaration_error
            elif rule.get("st_daily_buy_maximum") and bar.get("isST") == "1" and side == "buy" and requested + sum(t["quantity"] for t in trades if t["date"] == day and t["code"] == code and t["side"] == "buy") > rule["st_daily_buy_maximum"]:
                reason = "创业板 ST 单日累计买入超过 50 万股，未模拟拆单"
            elif side == "buy" and upper is not None and op >= upper:
                reason = "涨停开盘，保守不买入"
            elif side == "sell" and lower is not None and op <= lower:
                reason = "跌停开盘，保守不卖出"
            max_quantity = floor_quantity(D(previous["volume"] or 0) * D(cfg["participation"]), rule)
            executable = min(requested, max_quantity)
            if side == "sell":
                sellable = sum(l["quantity"] for l in lots[code] if l["bought"] < day and l["release"] <= day)
                executable = min(executable, sellable)
                if at and not sellable:
                    reason = reason or "T+1 或红股锁定，当前无可卖数量"
            price = round_price(op * (D(1) + D(cfg["slippage"]) * (1 if side == "buy" else -1)))
            price = max(D(rule["tick"]), price)
            if upper is not None:
                price = min(price, upper) if side == "buy" else max(price, lower)
            if side == "buy" and not reason:
                affordable = floor_quantity(cash / (price * (D(1) + D(cfg["commission_rate"]) + D(0 if etf else cfg["transfer_rate"]))), rule)
                if rule.get("board"):
                    affordable = min(affordable, requested)
                    while affordable > 0 and price * affordable + fee(price * affordable, side, day, cfg, rule)["total"] > cash:
                        affordable -= rule["lot"]
                    if affordable < rule["buy_minimum"]:
                        reason = f"资金不足最低 {rule['buy_minimum']} 股申报，未以小额委托成交"
                executable = min(executable, affordable)
                while executable > 0 and price * executable + fee(price * executable, side, day, cfg, rule)["total"] > cash:
                    executable -= rule["lot"]
            if side == "sell" and rule.get("board") == "gem" and executable != requested:
                executable = floor_quantity(D(executable), rule)
            if executable <= 0:
                reason = reason or "成交量上限、资金或可卖数量不足"
            order.update(execution_date=day, filled=0, status="rejected" if reason else "filled", reason=reason)
            if reason:
                orders.append(order)
                continue
            notional = price * executable
            fees = fee(notional, side, day, cfg, rule)
            if side == "buy":
                cash -= notional + fees["total"]
                lots[code].append({"quantity": executable, "bought": day, "release": day})
            else:
                remaining = executable
                for lot in lots[code]:
                    if lot["bought"] < day and lot["release"] <= day:
                        taken = min(remaining, lot["quantity"])
                        lot["quantity"] -= taken
                        remaining -= taken
                cash += notional - fees["total"]
            partial_reason = "剩余数量到期撤单"
            if side == "sell" and order.get("corporate_action_adjustment") and sellable < requested:
                partial_reason += "；可卖数量受 T+1 或红股上市日期限制"
            order.update(filled=executable, status="filled" if executable == requested else "partial", reason=None if executable == requested else partial_reason)
            orders.append(order)
            trade = {"date": day, "signal_date": order["signal_date"], "code": code, "side": side, "quantity": executable, "price": price, "notional": notional, "fees": fees}
            if at:
                trade.update(signal_at=order["signal_at"], execution_at=at.isoformat(), signal_period=order["signal_period"])
            trades.append(trade)
            if cash < 0:
                raise ValueError("现金账本出现负数，停止发布报告")
        pending = []

    if period_clock:
        first_open = datetime.combine(date.fromisoformat(calendar[0]), time(9, 30), ZoneInfo("Asia/Shanghai"))
        for event in period_clock.before(first_open):
            decide(event["at"], prior_calendar[-1], event["history"], event["period"])

    for index, day in enumerate(calendar):
        jobs.heartbeat(job["id"], index, len(calendar))
        # 除权日确认应收及锁定送股；派息/红股上市按各自日期处理。
        for code in codes:
            previous, current = histories[code][-1], bars[code][day]
            tick = D(asset_rules[code]["tick"])
            if quantity(code) and current.get("preclose") and abs(D(previous["close"])-D(current["preclose"])) > tick/2:
                events = [event for event in dividends[code] if event.get("dividOperateDate") == day]
                if len(events) != 1 or hashlib.sha256(db.dumps(events[0]).encode()).hexdigest() not in claims:
                    raise ValueError(f"{code} {day} 除权参考价变化缺少完整登记权益，可能是未覆盖公司行动；停止报告")
                event = events[0]
                ex_cash, reference_rule = reference_cash(event)
                expected = ((D(previous["close"])-ex_cash) / (D(1)+D(event.get("dividStocksPs") or 0)+D(event.get("dividReserveToStockPs") or 0))).quantize(tick, rounding=ROUND_HALF_UP)
                if abs(expected-D(current["preclose"])) > tick:
                    raise ValueError(f"{code} {day} 除权价与现金/送转股不一致，特殊公司行动尚未核验")
                if reference_rule:
                    warnings.add(f"{code} {day} 差异化分红：实际税前每股 {reference_rule['cash_per_share']} 元，摊薄除息每股 {reference_rule['reference_cash_per_share']} 元；核验公告见运行清单")
            for event in dividends[code]:
                key = hashlib.sha256(db.dumps(event).encode()).hexdigest()
                claim = claims.get(key)
                ex_date, pay_date = event.get("dividOperateDate"), event.get("dividPayDate")
                if claim and ex_date and ex_date <= day and not claim.get("ex_applied"):
                    claim["ex_applied"] = True
                    if claim["bonus"]:
                        release = event.get("dividStockMarketDate")
                        if not release:
                            raise ValueError(f"{code} 送转股缺少可交易日期，停止账本模拟")
                        lots[code].append({"quantity": claim["bonus"], "bought": claim["record_date"], "release": release})
                    cashflows.append({"date": ex_date, "code": code, "type": "除权确认", "cash": 0, "receivable": claim["cash"], "bonus": claim["bonus"]})
                if claim and claim.get("ex_applied") and not claim.get("paid"):
                    if claim["cash"] and not pay_date:
                        raise ValueError(f"{code} 分红缺少派息日")
                    if not claim["cash"] or pay_date <= day:
                        cash += claim["cash"]
                        claim["paid"] = True
                        cashflows.append({"date": pay_date or ex_date, "code": code, "type": "派息到账", "cash": claim["cash"]})
        if not minute_clock:
            execute({c: bars[c][day] for c in codes}, {c: histories[c][-1] for c in codes}, day)
        else:
            for opened, closed in minute_clock.slots(day):
                frame = minute_clock.frame(day, closed)
                opening = {c: {**frame[c], "preclose": bars[c][day]["preclose"], "tradestatus": bars[c][day].get("tradestatus"), "isST": bars[c][day].get("isST")} if frame[c] is not None else None for c in codes}
                execute(opening, {c: minute_clock.history[c][-1] for c in codes}, day, opened)
                minute_clock.complete(frame)
                prices = {c: frame[c]["close"] if frame[c] is not None else bars[c][day]["preclose"] for c in codes}
                nav = account(prices)
                intraday_equity.append({"at": closed.isoformat(), "date": day, "nav": cents(nav), "cash": cents(cash), "frozen_cash": D(0), "receivable": cents(receivable()), "value": float(nav / initial), "positions": {c: quantity(c) for c in codes}, "sellable": {c: sum(l["quantity"] for l in lots[c] if l["bought"] < day and l["release"] <= day) for c in codes}})
                if any(bar is not None for bar in frame.values()):
                    decide(closed, day, minute_clock.history, closed.isoformat(), prices, frame)
        for code in codes:
            histories[code].append(bars[code][day])
            histories[code] = histories[code][-daily_lookback:]
            for event in dividends[code]:
                if event.get("dividRegistDate") == day and quantity(code):
                    key = hashlib.sha256(db.dumps(event).encode()).hexdigest()
                    gross = D(event.get("dividCashPsBeforeTax") or 0) * quantity(code)
                    bonus_entitlement = (D(event.get("dividStocksPs") or 0) + D(event.get("dividReserveToStockPs") or 0)) * quantity(code)
                    bonus = int(bonus_entitlement)
                    if bonus_entitlement != bonus:
                        raise ValueError(f"{code} 送转股存在小数权益，尚未核验零碎股结算，停止发布报告")
                    tax = asset_rules[code].get("dividend_tax", cfg["dividend_tax"])
                    claims[key] = {"cash": cents(gross * (D(1) - D(tax))), "bonus": bonus, "record_date": day}
        close_prices = {c: bars[c][day]["close"] for c in codes}
        nav = account(close_prices)
        sellable = {c: sum(l["quantity"] for l in lots[c] if l["bought"] < day and l["release"] <= day) for c in codes}
        equity.append({"date": day, "nav": cents(nav), "cash": cents(cash), "frozen_cash": D(0), "receivable": cents(receivable()), "value": float(nav / initial),
                       "benchmark": benchmark_by_day[day]["close"] / benchmark_base,
                       "positions": {c: quantity(c) for c in codes}, "sellable": sellable})
        if frequency == "d":
            decide(ready_at(day, "d"), day, histories)
        elif period_clock:
            through = datetime.combine(date.fromisoformat(calendar[index+1]), time(9, 30), ZoneInfo("Asia/Shanghai")) if index+1 < len(calendar) else datetime.combine(date.fromisoformat(end), time.max, ZoneInfo("Asia/Shanghai"))
            for event in period_clock.before(through):
                decide(event["at"], day, event["history"], event["period"])
    for order in pending:
        orders.append({**order, "status": "expired", "filled": 0, "execution_date": None, "reason": "回测结束，未使用区间外价格"})
    result = {"metrics": metrics(equity, trades, initial, cfg["risk_free"]), "equity": equity,
              "orders": orders, "trades": trades, "cashflows": cashflows,
              "positions": {c: quantity(c) for c in codes}, "warnings": sorted(warnings),
              "benchmark_type": "价格指数，不包含现金分红", "sampling": "日末净值",
              "signal_frequency": frequency, "signal_events": signal_log,
              "period_validation": {"amount_tolerance_yuan": 1, "amount_differences": period_clock.amount_differences} if period_clock else None,
              "cash_policy": "下一开盘即时检查现金并结算，待执行目标不预先冻结现金；每日冻结金额为零",
              "data_version": version, "universe_type": "用户指定固定股票池，不宣称历史全市场无幸存者偏差"}
    if any(rule["kind"] == "etf" for rule in asset_rules.values()):
        result.update(asset_rules=asset_rules, supplemental_actions=run.get("supplemental_actions", []),
                      universe_type="用户指定固定证券池，不宣称历史全市场无幸存者偏差")
    if ipo_days:
        result.update(asset_rules=asset_rules, ipo_unlimited_days={c: sorted(days) for c, days in ipo_days.items()},
                      corporate_actions=[event for events in dividends.values() for event in events])
    if minute_clock:
        peak, drawdown = initial, D(0)
        for row in intraday_equity:
            peak = max(peak, row["nav"])
            drawdown = max(drawdown, (peak-row["nav"])/peak)
        result["metrics"].update(daily_max_drawdown=result["metrics"]["max_drawdown"], max_drawdown=float(drawdown),
                                 drawdown_sampling="分钟 bar 收盘净值和初始本金，不含 bar 内路径")
        result.update(intraday_equity=intraday_equity, minute_validation={"expected_bars_per_regular_day": 240//int(frequency),
                      "amount_tolerance_yuan": 1, "amount_differences": minute_clock.amount_differences,
                      "range_policy": "开收盘一致、分钟范围包含于日线范围、成交量一致；未覆盖日线极值逐项披露，不重建触价成交",
                      "range_differences": minute_clock.range_differences},
                      sampling="日末年化/基准；另存分钟 bar 收盘净值与分钟最大回撤",
                      cash_policy="下一 bar 开盘即时检查现金并结算；待执行目标不预先冻结现金，分钟及日末冻结金额为零")
        if minute_clock.close_differences:
            result["minute_validation"].update(close_differences=minute_clock.close_differences,
                range_policy="开盘与量一致；旧沪市ETF官方日收盘可与末分钟价不同，但须位于最后bar范围内；两种估值与差异分别保留")
    if financial_reports:
        result["financial_reports"] = list(financial_reports.values())
    if universe_input:
        result['universe_snapshots'] = list(universe_references.values())
        result['universe_input_note'] = UNIVERSE_NOTE
    jobs.heartbeat(job["id"], len(calendar), len(calendar))
    db.execute("UPDATE backtest_runs SET result=%s WHERE id=%s", (db.jb(result), job["id"]))
    jobs.log(job["id"], f"回测完成：{len(calendar)} 个交易日，{len(signal_log)} 次 {frequency} 信号，{len(trades)} 笔成交；数据版本 {version}")

