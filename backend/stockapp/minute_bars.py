"""固定版本的完整竞价时段分钟输入；市场模拟时刻与盘后采集就绪分开。"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .market import price_series, ready_at

FREQUENCIES = {"5", "15", "30", "60"}
TZ = ZoneInfo("Asia/Shanghai")


class MinuteBars:
    def __init__(self, codes, frequency, lookback, start, end, version, as_of, trading, daily, asset_rules=None):
        self.codes, self.frequency, self.lookback = codes, frequency, lookback
        self.by_day, self.history, self.amount_differences, self.range_differences = {}, {}, [], []
        self.close_differences = []
        for day in (d for d in trading if start <= d <= end):
            if ready_at(day, frequency) > as_of:
                raise ValueError(f"{day} 分钟数据尚未到盘后就绪时刻，请选择已完成交易日")
        for code in codes:
            source = price_series(code, frequency, version=version, end=end)
            groups, ordered = {}, []
            for bar in source:
                stamp = bar.get("time", "")
                if len(stamp) != 17 or not stamp.isdigit() or stamp[-3:] != "000":
                    raise ValueError(f"{code} 分钟时间字段不符合完整周期格式")
                at = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
                if at.date().isoformat() != bar["date"]:
                    raise ValueError(f"{code} 分钟日期与时间字段冲突")
                if bar["date"] not in daily[code]:
                    raise ValueError(f"{code} {bar['date']} 分钟行情缺少日线基准")
                enriched = {**bar, "bar_end": at.isoformat(),
                            "signal_scale": daily[code][bar["date"]]["signal_scale"]}
                rule = (asset_rules or {}).get(code, {})
                if (rule.get("kind") == "etf" or rule.get("board")) and any(Decimal(str(bar[field])) % Decimal(rule["tick"]) for field in ("open", "high", "low", "close")):
                    raise ValueError(f"{code} {bar['date']} 分钟报价不符合 {rule['tick']} 元档位")
                enriched["signal_close"] = enriched["close"] * enriched["signal_scale"]
                groups.setdefault(bar["date"], []).append(enriched)
                ordered.append(enriched)
            ordered.sort(key=lambda b: b["bar_end"])
            warm = [b for b in ordered if b["date"] < start][-lookback:]
            if len(warm) < lookback:
                raise ValueError(f"{code} 的分钟预热不足，请补采更早分钟历史")
            self.history[code] = warm
            required = [d for d in trading if warm[0]["date"] <= d <= end]
            for day in required:
                base = daily[code].get(day)
                if base is None:
                    raise ValueError(f"{code} {day} 缺少分钟核对所需日线")
                rows = sorted(groups.get(day, []), key=lambda b: b["bar_end"])
                if not rows and base.get("tradestatus") == "0":
                    self.by_day.setdefault(day, {})[code] = {}
                    continue
                expected = [close.isoformat() for _, close in self.slots(day)]
                if [b["bar_end"] for b in rows] != expected:
                    raise ValueError(f"{code} {day} 的 {frequency} 分钟边界/数量不完整；未跨午休合成或补零")
                aggregates = {"open": rows[0]["open"], "close": rows[-1]["close"],
                              "high": max(b["high"] for b in rows), "low": min(b["low"] for b in rows)}
                for field, value in aggregates.items():
                    difference = Decimal(str(value))-Decimal(str(base[field]))
                    if abs(difference) <= Decimal("0.00001"):
                        continue
                    rule = (asset_rules or {}).get(code, {})
                    # 旧沪市基金日收盘为末一分钟加权价，不能要求等于分钟最后成交价。
                    if (field == "close" and rule.get("kind") == "etf"
                            and day < rule["close_policy"]["change_date"]
                            and rows[-1]["low"] <= base["close"] <= rows[-1]["high"]):
                        self.close_differences.append({"code": code, "date": day, "minute": value,
                            "daily": base[field], "difference": str(difference),
                            "policy": rule["close_policy"]["before"]})
                        continue
                    # 来源分钟极值可能没有覆盖日线极值；不把日线极值注入历史分钟。
                    if (field == "high" and difference < 0) or (field == "low" and difference > 0):
                        self.range_differences.append({"code": code, "date": day, "field": field,
                            "minute": value, "daily": base[field], "difference": str(difference)})
                    else:
                        raise ValueError(f"{code} {day} 分钟 {field} 与日线冲突")
                for field, tolerance in (("volume", Decimal(0)), ("amount", Decimal(1))):
                    total = sum((Decimal(str(b[field])) for b in rows), Decimal(0))
                    difference = total-Decimal(str(base[field]))
                    if abs(difference) > tolerance:
                        raise ValueError(f"{code} {day} 分钟 {field} 与日线汇总不符")
                    if field == "amount" and difference:
                        self.amount_differences.append({"code": code, "date": day, "difference": str(difference)})
                if base.get("tradestatus") == "0" and any(b["volume"] for b in rows):
                    raise ValueError(f"{code} {day} 停牌标记与分钟成交量冲突")
                self.by_day.setdefault(day, {})[code] = {b["bar_end"]: b for b in rows}

    def slots(self, day):
        step = timedelta(minutes=int(self.frequency))
        for hour, minute in ((9, 30), (13, 0)):
            begin = datetime.combine(date.fromisoformat(day), time(hour, minute), TZ)
            for index in range(120//int(self.frequency)):
                yield begin+step*index, begin+step*(index+1)

    def frame(self, day, close):
        return {code: self.by_day[day][code].get(close.isoformat()) for code in self.codes}

    def complete(self, frame):
        for code, bar in frame.items():
            if bar is not None:
                self.history[code].append(bar)
                self.history[code] = self.history[code][-self.lookback:]
