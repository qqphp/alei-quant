from decimal import Decimal, InvalidOperation
from bisect import bisect_right
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo

from . import db
from .code_changes import published_markers
from .rvc001 import ema, attach_chart_indicator


def ready_at(day, frequency):
    day = date.fromisoformat(day)
    clock = time(17, 30)
    if frequency in {"5", "15", "30", "60"}:
        clock = time(20)
    elif frequency == "w":
        day += timedelta(days=(5-day.weekday()) % 7)
    elif frequency == "m":
        day = (day.replace(day=28)+timedelta(days=4)).replace(day=1)
    return datetime.combine(day, clock, ZoneInfo("Asia/Shanghai"))


def price_series(code, frequency="d", version=None, end=None):
    boundary = end + "T23:59:59.999" if end and frequency in {"5", "15", "30", "60"} else end
    source = db.payloads("bars:" + frequency, code=code, version=version, end=boundary)
    scale, previous_close, output = 1.0, None, []
    ema3 = ema8 = None
    for row in source:
        bar = dict(row)
        for key in ("open", "high", "low", "close", "preclose", "volume", "amount", "pctChg", "turn", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"):
            bar[key] = float(row[key]) if row.get(key) not in (None, "") else None
        # 涨跌幅复权：基准为指定版本中最早一根行情，之后只使用已结束 bar 的前收盘参考价。
        if frequency == "d" and previous_close and bar["preclose"]:
            scale *= previous_close / bar["preclose"]
        bar["signal_close"] = bar["close"] * scale
        bar["signal_scale"] = scale
        ema3 = ema(ema3, bar["signal_close"], 3)
        ema8 = ema(ema8, bar["signal_close"], 8)
        bar.update(signal_ema3=ema3, signal_ema8=ema8)
        previous_close = bar["close"]
        output.append(bar)
    return output


def chart(code, frequency, start, end, adjustment, limit=10000, version=None, rvc001_red_exit="pink_or_green"):
    version = db.latest_version() if version is None else version
    data = price_series(code, frequency, version=version, end=end)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    not_ready = sum(start <= b["date"] <= end and ready_at(b["date"], frequency) > now for b in data)
    data = [b for b in data if ready_at(b["date"], frequency) <= now]
    if adjustment != "raw" and frequency != "d":
        raise ValueError("其它周期的复权因子对齐尚待验证，目前支持原始行情")
    attach_chart_indicator(data, start, rvc001_red_exit)
    factor_version, factors, factor_dates = None, [], []
    if data and adjustment != "raw":
        coverage = db.one("SELECT id FROM data_batches WHERE method='query_adjust_factor' AND status='published' AND id<=%s "
                          "AND arguments->>'code'=%s AND arguments->>'start_date'<=%s AND arguments->>'end_date'>=%s "
                          "ORDER BY id DESC LIMIT 1", (version, code, data[0]["date"], data[-1]["date"]))
        if coverage:
            candidates = db.payloads("factors", code=code, version=coverage["id"])
            if candidates and candidates[0]["dividOperateDate"] <= data[0]["date"]:
                factor_field = "foreAdjustFactor" if adjustment == "forward" else "backAdjustFactor"
                try:
                    invalid = any(not Decimal(row[factor_field]).is_finite() or Decimal(row[factor_field]) <= 0 for row in candidates)
                except (InvalidOperation, KeyError, TypeError):
                    invalid = True
                if invalid:
                    raise ValueError("复权因子非正或非有限值，请检查来源批次")
                factor_version, factors = coverage["id"], candidates
                factor_dates = [row["dividOperateDate"] for row in factors]
    normalization = data[-1]["signal_scale"] if data and adjustment == "forward" else 1
    for bar in data:
        if factors:
            factor = float(factors[bisect_right(factor_dates, bar["date"]) - 1][factor_field])
        else:
            factor = bar["signal_scale"] / normalization if adjustment != "raw" else 1
        bar["display_scale"] = factor
        for field in ("open", "high", "low", "close"):
            bar[field] *= factor
    closes = [b["close"] for b in data]
    ema12 = ema26 = dea = None
    for i, bar in enumerate(data):
        close = closes[i]
        for size in (5, 10, 20, 30, 60):
            bar["ma" + str(size)] = sum(closes[i-size+1:i+1]) / size if i + 1 >= size else None
        ema12 = close if ema12 is None else ema12 * 11/13 + close * 2/13
        ema26 = close if ema26 is None else ema26 * 25/27 + close * 2/27
        dif = ema12 - ema26
        dea = dif if dea is None else dea * 8/10 + dif * 2/10
        bar.update(dif=dif, dea=dea, macd=2 * (dif - dea))
    selected = [b for b in data if start <= b["date"] <= end]
    if factors:
        price_note = "官方累积因子展示：逐日原始 OHLC × 最近已生效因子；前复权沿用该来源批次的最新归一，可能包含所选区间之后的事件，仅供展示；后复权沿用官方上市基准。"
    elif adjustment != "raw":
        price_note = "因子区间覆盖不足，显示涨跌幅连续序列：后复权以最早已入库行情为基准，前复权按所选结束日归一；绝对价格不等同官方复权返回。"
    else:
        price_note = "未复权原始价格。"
    markers = published_markers(code, start, end, version) if frequency == "d" and code == "sz.300114" else []
    if markers:
        price_note += " 2025-02-17 旧代码变更非价格标记已原样保留，未生成该日 OHLC；2025-02-17 起代码为 sz.302132，尚未合并价格或转换回测持仓。"
    return {"code": code, "frequency": frequency, "adjustment": adjustment,
            "total": len(selected), "data": selected[-limit:], "truncated": len(selected) > limit,
            "not_ready": not_ready, "non_price_markers": markers,
            "adjustment_source": "official_factors" if factors else "raw" if adjustment == "raw" else "continuous",
            "factor_version": factor_version, "price_note": price_note,
            "rvc001_red_exit": rvc001_red_exit,
            "rvc001_note": ("RVC001：日线使用平台连续信号价格，与阿雷红变/绿变策略一致，不随主图复权切换。" if frequency == "d" else
                            "RVC001：使用本周期原始信号价格；阿雷红变/绿变回测策略仅支持日线。") +
                           ("红变出场：首次粉K或绿K；绿变出场：首次绿K。" if rvc001_red_exit == "pink_or_green" else
                            "旧版红变出场：首次粉K；绿变出场：首次绿K。") +
                           "红箭头为金叉，粉/绿箭头为区间起点空仓后两套规则的出场信号，小标记为原公式死叉；信号不表示已经成交。",
            "algorithm": price_note + " MACD 12/26/9，柱线为 2*(DIF-DEA)",
            "version": version}

