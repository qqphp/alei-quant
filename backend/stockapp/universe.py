"""完整返回的某日证券池；成员集合不借用当前名单，名称仅按源回溯返回。"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db
from .market import ready_at


# 仅用于回填范围分类；不补写 BaoStock 档案、历史名称、可交易状态或行情。
CLASSIFICATION_REFERENCES = {
    "sh.516480": {
        "type": "5",
        "source_url": "https://www.sse.com.cn/disclosure/announcement/listing/c/c_20251014_10794536.shtml",
        "source_date": "2025-10-14",
        "document": "docs/research/516480-termination.html",
        "note": "上交所明确为交易型开放式指数证券投资基金；BaoStock 档案为空，仅补充资产类别",
    }
}


def classify_pool(pool, version):
    types = {row["code"]: row.get("type") for row in db.payloads("instruments", version=version)}
    references = {code: dict(CLASSIFICATION_REFERENCES[code]) for code in pool
                  if not types.get(code) and code in CLASSIFICATION_REFERENCES}
    types.update({code: ref["type"] for code, ref in references.items()})
    unknown = [code for code in pool if not types.get(code)]
    if unknown:
        raise ValueError("证券池存在未入库证券档案，无法完整识别资产类型；请先补采 " + "、".join(unknown[:10]))
    return types, references


def trading_days(start, end, version):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or last > date.today():
        raise ValueError("请检查证券池日期范围，结束日期不得在未来")
    calendar = db.payloads("calendar", start=start, end=end, version=version)
    expected = {(first+timedelta(days=n)).isoformat() for n in range((last-first).days+1)}
    if {r["calendar_date"] for r in calendar} != expected:
        raise ValueError("区间交易日历不完整，请先采集日历；不按现有日历缩短范围")
    if any(r["is_trading_day"] not in {"0", "1"} for r in calendar):
        raise ValueError("交易日历状态异常")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    days = [r["calendar_date"] for r in calendar if r["is_trading_day"] == "1"]
    if any(ready_at(day, "d") > now for day in days):
        raise ValueError("区间包含尚未达到日线就绪时刻的交易日")
    if not days:
        raise ValueError("区间没有交易日")
    return days


def snapshots(start, end, version):
    return db.rows("SELECT DISTINCT ON(s.day) s.day::text,s.batch_id,s.codes FROM universe_snapshots s "
                   "JOIN data_batches b ON b.id=s.batch_id WHERE b.status='published' AND s.day BETWEEN %s AND %s "
                   "AND s.batch_id<=%s ORDER BY s.day,s.batch_id DESC", (start,end,version))


def coverage(start, end, version):
    days = trading_days(start,end,version)
    available = {r["day"] for r in db.rows(
        "SELECT DISTINCT s.day::text FROM universe_snapshots s JOIN data_batches b ON b.id=s.batch_id "
        "WHERE b.status='published' AND s.day BETWEEN %s AND %s AND s.batch_id<=%s", (start,end,version))}
    missing = [day for day in days if day not in available]
    return {"start":start,"end":end,"version":version,"trading_days":len(days),
            "published_days":len(days)-len(missing),"missing_days":missing,
            "complete":not missing}


def snapshot(day, version):
    row = snapshots(day,day,version)
    if not row:
        raise ValueError(f"{day} 没有完整已发布证券池快照，未改用当前或相邻日名单")
    return row[0]


def members(start, end, version):
    status = coverage(start,end,version)
    if status["missing_days"]:
        raise ValueError(f"历史证券池缺少 {len(status['missing_days'])} 个交易日，先回填证券池；例如 " +
                         "、".join(status["missing_days"][:5]))
    return sorted({code for row in snapshots(start,end,version) for code in row["codes"]})


def page(day, version, query="", page=1, size=100):
    source = snapshot(day,version)
    quality = db.one("SELECT coverage FROM data_batches WHERE id=%s", (source["batch_id"],))["coverage"]
    # 固定完整快照的成员，再读取其发布时的最后已知行；后来移除的成员不会从旧行复活。
    latest = ("SELECT r.payload FROM unnest(%s::text[]) AS member(code) JOIN LATERAL "
              "(SELECT r.payload FROM source_records r JOIN data_batches b ON b.id=r.batch_id "
              "WHERE r.dataset='universe' AND r.record_key=%s||'|'||member.code "
              "AND r.batch_id<=%s AND b.status='published' ORDER BY r.batch_id DESC LIMIT 1) r ON true")
    args = [source["codes"],day,source["batch_id"]]
    where = " WHERE position(lower(%s) in lower(payload->>'code'))>0 OR position(lower(%s) in lower(payload->>'code_name'))>0"
    args += [query,query]
    with db.connect() as conn:
        total = conn.execute("SELECT count(*) AS total FROM ("+latest+") p"+where,args).fetchone()["total"]
        data = conn.execute("SELECT payload FROM ("+latest+") p"+where+" ORDER BY payload->>'code' LIMIT %s OFFSET %s",
                            args+[size,(page-1)*size]).fetchall()
    return {"day":day,"version":version,"source_batch":source["batch_id"],"members":len(source["codes"]),
            "total":total,"data":[r["payload"] for r in data],"page":page,
            "missing_name_count":len(quality["universe_missing_names"]) if "universe_missing_names" in quality else None,
            "note":"官方指定日来源回溯快照；名称、成员和交易状态均不保证当年首次发布原版。实际新代码在正式启用前已被来源返回；不能单独作为当时真实可交易的证明。名称空缺保持原值，不当作历史简称或用于识别当时ST；完整接收不代表行情完整。"}
