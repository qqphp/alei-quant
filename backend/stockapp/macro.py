"""宏观浏览的来源覆盖与历史使用边界；不改写来源数值。"""
from datetime import date
from decimal import Decimal, InvalidOperation


MACRO_POLICIES = {
    "deposit_rate": {"clock_fields": ["pubDate"], "reason": "源 pubDate 在已核对样本对应调整生效日，不能当作已核验的公告时钟；完整政策与原版修订未获验证，不提供给历史策略。"},
    "loan_rate": {"clock_fields": ["pubDate"], "reason": "源 pubDate 在已核对样本对应调整生效日，不能当作已核验的公告时钟；基准贷款利率不是 LPR 或今日银行报价，不提供给历史策略。"},
    "reserve_ratio": {"clock_fields": ["pubDate", "effectiveDate"], "reason": "公告日与执行日分别保留，公告事件不能直接当作当日有效准备金率；完整政策序列及修订未获验证，不提供给历史策略。"},
    "money_month": {"clock_fields": ["statYear", "statMonth"], "reason": "只有统计年月，没有可证实发布日期；不按月末或抓取日回填历史可用时间，不提供给历史策略。"},
    "money_year": {"clock_fields": ["statYear"], "reason": "只有统计年份，没有可证实发布日期；不按年末或抓取日回填历史可用时间，不提供给历史策略。"},
}


def policy(dataset):
    if dataset not in MACRO_POLICIES:
        return None
    return {"historical_use": "browse_only", "label": "仅供浏览 / 导出", **MACRO_POLICIES[dataset],
            "note": "接口成功不证明序列覆盖当前日期；末期只表示来源记录，不表示现行政策。空值不当作零，固定本地版本不保证历史首次发布原值。"}


def valid_date(value):
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except (ValueError, TypeError):
        return None


def describe(dataset, rows):
    support = policy(dataset)
    if support is None:
        return None
    periods, publications, effective = [], [], []
    fields = sorted(set().union(*(row.keys() for row in rows)))
    values = {field: {"valid": 0, "missing": 0, "invalid": 0, "zero": 0, "latest_valid_period": None}
              for field in fields if field not in {"pubDate", "effectiveDate", "statYear", "statMonth"}}
    invalid_clocks = 0
    for row in rows:
        published = valid_date(row.get("pubDate"))
        if dataset in {"money_month", "money_year"}:
            year, month = row.get("statYear"), row.get("statMonth")
            try:
                candidate = year + ("-" + month.zfill(2) if dataset == "money_month" else "")
                checked = candidate + ("-01" if dataset == "money_month" else "-01-01")
                period = candidate if valid_date(checked) else None
            except (TypeError, AttributeError):
                period = None
        else:
            period = published
        invalid_clocks += period is None
        if period:
            periods.append(period)
        if published:
            publications.append(published)
        if dataset == "reserve_ratio":
            day = valid_date(row.get("effectiveDate"))
            invalid_clocks += day is None
            if day:
                effective.append(day)
        for field, counts in values.items():
            value = row.get(field)
            if value in (None, "", "--"):
                counts["missing"] += 1
                continue
            try:
                number = Decimal(value)
                if not number.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError, TypeError):
                counts["invalid"] += 1
                continue
            counts["valid"] += 1
            counts["zero"] += number == 0
            if period and (counts["latest_valid_period"] is None or period > counts["latest_valid_period"]):
                counts["latest_valid_period"] = period
    return {**support, "rows": len(rows), "earliest_period": min(periods, default=None),
            "latest_period": max(periods, default=None), "latest_pubDate": max(publications, default=None),
            "latest_effectiveDate": max(effective, default=None), "invalid_clock_fields": invalid_clocks,
            "values": values}
