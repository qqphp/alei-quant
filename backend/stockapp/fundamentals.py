"""按公告/更新日期过滤固定数据版本；不声称恢复首次披露原值。"""
from copy import deepcopy
from datetime import date

from . import db
from .plugins import FINANCIAL_DATASETS


def availability(dataset, row):
    if dataset == "express":
        published, updated, period = row.get("performanceExpPubDate"), row.get("performanceExpUpdateDate"), row.get("performanceExpStatDate")
    elif dataset == "forecast":
        published, updated, period = row.get("profitForcastExpPubDate"), None, row.get("profitForcastExpStatDate")
    else:
        published, updated, period = row.get("pubDate"), None, row.get("statDate")
    if not published or not period:
        raise ValueError(f"{dataset} 缺少公告日期或报告期，不能提供给历史策略")
    try:
        for value in (published, updated, period):
            if value:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError("要求 YYYY-MM-DD")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{dataset} 的公告/更新日期不是有效日期") from exc
    return max(published, updated or published), period


def load_financial(manifest, codes, version):
    loaded = {}
    for dataset in sorted(set(manifest.get("datasets", [])) & FINANCIAL_DATASETS):
        loaded[dataset] = {}
        for code in codes:
            records = db.payloads(dataset, code=code, version=version)
            if not records:
                raise ValueError(f"{code} 尚未采集模型必需的 {dataset} 数据")
            loaded[dataset][code] = [(availability(dataset, row), row) for row in records]
    return loaded


def visible_financial(loaded, manifest, day):
    data = {}
    for dataset, by_code in loaded.items():
        data[dataset] = {}
        for code, records in by_code.items():
            latest = {}
            for (after, period), row in records:
                # 没有公告时刻，统一推迟到日期之后的首个模拟交易日。
                if after >= day or period > day:
                    continue
                key = (period, row.get("profitForcastType", ""))
                if key not in latest or after > latest[key][0]:
                    latest[key] = (after, row)
            if not latest:
                raise ValueError(f"{code} 在 {day} 尚无当时可用的 {dataset}；未以未来财报填补")
            selected = [row for _, row in sorted(latest.values(), key=lambda item: (availability(dataset, item[1])[1], item[0]))]
            for field in manifest.get("required_fields", {}).get(dataset, []):
                if any(row.get(field) in (None, "", "--") for row in selected):
                    raise ValueError(f"{code} 在 {day} 的 {dataset}.{field} 缺失，停止而不置零")
            data[dataset][code] = deepcopy(selected)
    return data
