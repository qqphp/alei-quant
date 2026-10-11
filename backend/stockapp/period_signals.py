"""官方完整周期按就绪时刻进入策略窗口；日线继续承担成交和账户估值。"""
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal
import math
from zoneinfo import ZoneInfo

from .market import price_series, ready_at

TZ = ZoneInfo("Asia/Shanghai")


class PeriodSignals:
    def __init__(self, codes, frequency, lookback, start, end, version, as_of, trading, daily):
        self.lookback, self.cursor = lookback, 0
        self.amount_differences = []
        start_at = datetime.combine(date.fromisoformat(start), time.min, TZ)
        cutoff = min(datetime.combine(date.fromisoformat(end), time.max, TZ), datetime.fromisoformat(as_of))
        groups = {}
        for day in trading:
            value = date.fromisoformat(day)
            key = value.isocalendar()[:2] if frequency == "w" else (value.year, value.month)
            groups.setdefault(key, []).append(day)
        periods = [(days[-1], ready_at(days[-1], frequency), days) for days in groups.values()
                   if ready_at(days[-1], frequency) <= cutoff]
        warmup = [p for p in periods if p[1] < start_at][-lookback:]
        if len(warmup) < lookback:
            raise ValueError("交易日历不覆盖完整周期预热窗口，请采集更早日历")
        active = [p for p in periods if p[1] >= start_at]
        self.history, validated = {}, {}
        for code in codes:
            source = {b["date"]: b for b in price_series(code, frequency, version=version, end=end)}
            validated[code] = {}
            for period, available, days in warmup + active:
                bar = source.get(period)
                if bar is None:
                    raise ValueError(f"{code} 缺少 {frequency} 周期 {period}；未用日线静默替换官方序列")
                missing = [day for day in days if day not in daily[code]]
                if missing:
                    raise ValueError(f"{code} 周期 {period} 缺少日线 {missing[0]}，无法核对周期和连续价格")
                underlying = [daily[code][day] for day in days]
                if any(not b.get("preclose") or not math.isfinite(b["preclose"]) or b["preclose"] <= 0 for b in underlying):
                    raise ValueError(f"{code} 周期 {period} 缺少可靠前收盘基准")
                expected = {"open": underlying[0]["open"], "close": underlying[-1]["close"],
                            "high": max(b["high"] for b in underlying), "low": min(b["low"] for b in underlying)}
                for field, value in expected.items():
                    if abs(Decimal(str(bar[field]))-Decimal(str(value))) > Decimal("0.00001"):
                        raise ValueError(f"{code} 官方周期 {period} 的 {field} 与日线汇总不符，停止信号计算")
                for field, tolerance in (("volume", Decimal("0")), ("amount", Decimal("1"))):
                    value = sum((Decimal(str(b[field])) for b in underlying), Decimal(0))
                    if bar.get(field) is None or abs(Decimal(str(bar[field]))-value) > tolerance:
                        raise ValueError(f"{code} 官方周期 {period} 的 {field} 与日线汇总不符")
                    if field == "amount" and Decimal(str(bar[field])) != value:
                        self.amount_differences.append({"code": code, "period": period,
                                                        "official": str(bar[field]), "daily_sum": str(value),
                                                        "difference": str(Decimal(str(bar[field]))-value)})
                enriched = dict(bar)
                enriched.update(signal_close=underlying[-1]["signal_close"], signal_scale=underlying[-1]["signal_scale"],
                                available_at=available.isoformat())
                validated[code][period] = enriched
            self.history[code] = [validated[code][p[0]] for p in warmup]
        self.events = [{"period": period, "at": available,
                        "bars": {code: validated[code][period] for code in codes}} for period, available, _ in active]

    def before(self, through):
        while self.cursor < len(self.events) and self.events[self.cursor]["at"] < through:
            event = self.events[self.cursor]
            for code, bar in event["bars"].items():
                self.history[code].append(bar)
                self.history[code] = self.history[code][-self.lookback:]
            self.cursor += 1
            yield {**event, "history": deepcopy(self.history)}
