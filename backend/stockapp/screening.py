"""市场筛选使用最新发布行情投影，历史回测仍读取版本化源记录。"""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

from . import db


class MarketFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    q: str = Field("", max_length=100)
    type: Literal["", "1", "2", "3", "4", "5"] = ""
    exchange: Literal["", "sh", "sz"] = ""
    status: Literal["", "0", "1"] = ""
    industry: str = Field("", max_length=100)
    sort: Literal["code", "code_name", "close", "pctChg", "peTTM", "pbMRQ", "amount"] = "code"
    direction: Literal["asc", "desc"] = "asc"
    watch_only: bool = False
    with_bars: bool = False
    pe_min: float | None = None
    pe_max: float | None = None
    pb_min: float | None = None
    pb_max: float | None = None
    amount_min: float | None = Field(None, ge=0)


def search(filters, page, size):
    conditions, args = [], []
    if filters.q:
        query = filters.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("((i.payload->>'code')||' '||(i.payload->>'code_name')) ILIKE %s")
        args.append("%" + query + "%")
    for condition, value in (("i.payload->>'type'=%s", filters.type),
                             ("split_part(i.code,'.',1)=%s", filters.exchange),
                             ("i.payload->>'status'=%s", filters.status),
                             ("industry.payload->>'industry'=%s", filters.industry)):
        if value:
            conditions.append(condition)
            args.append(value)
    if filters.watch_only:
        conditions.append("w.code IS NOT NULL")
    if filters.with_bars:
        conditions.append("q.code IS NOT NULL")
    for column, low, high in (("pe_ttm", filters.pe_min, filters.pe_max), ("pb_mrq", filters.pb_min, filters.pb_max)):
        if low is not None and high is not None and low > high:
            raise ValueError("筛选下限不得超过上限")
        for operator, value in ((">=", low), ("<=", high)):
            if value is not None:
                conditions.append(f"q.{column}{operator}%s")
                args.append(value)
    if filters.amount_min is not None:
        conditions.append("q.amount>=%s")
        args.append(filters.amount_min)
    column = {"code": "code", "code_name": "code_name", "close": "close", "pctChg": "pct_chg", "peTTM": "pe_ttm", "pbMRQ": "pb_mrq", "amount": "amount"}[filters.sort]
    order = f"{column} {filters.direction} NULLS LAST" + (",code ASC" if column != "code" else "")
    query = """WITH instruments AS (
      SELECT DISTINCT ON(r.code) r.code,r.payload FROM source_records r JOIN data_batches b ON b.id=r.batch_id
      WHERE r.dataset='instruments' AND b.status='published' ORDER BY r.code,r.batch_id DESC
    ), industry AS (
      SELECT DISTINCT ON(r.code) r.code,r.payload FROM source_records r JOIN data_batches b ON b.id=r.batch_id
      WHERE r.dataset='industry' AND b.status='published' ORDER BY r.code,r.period DESC,r.batch_id DESC
    ), filtered AS (
      SELECT i.code,i.payload->>'code_name' AS code_name,i.payload AS basic,q.payload AS quote,w.code IS NOT NULL AS watched,
             q.close,q.pct_chg,q.pe_ttm,q.pb_mrq,q.amount
      FROM instruments i LEFT JOIN latest_quotes q ON q.code=i.code LEFT JOIN watchlist w ON w.code=i.code
      LEFT JOIN industry ON industry.code=i.code WHERE """ + (" AND ".join(conditions) or "TRUE") + ") "
    query += "SELECT count(*) AS total,COALESCE((SELECT jsonb_agg(to_jsonb(p)) FROM (SELECT basic,quote,watched FROM filtered ORDER BY " + order + " LIMIT %s OFFSET %s) p),'[]'::jsonb) AS data FROM filtered"
    result = db.one(query, [*args, size, (page - 1) * size])
    result["data"] = [{**item["basic"], "quote": item["quote"], "watched": item["watched"]} for item in result["data"]]
    return {**result, "page": page, "size": size}
