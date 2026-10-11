"""同一已核验完整交易日的市场统计；不把各证券最新报价混合排名。"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .market import ready_at


INDICES = ["sh.000001", "sh.000300", "sh.000905", "sz.399001"]
BASIC = """WITH basic AS (
  SELECT DISTINCT ON(r.code) r.code,r.payload FROM source_records r JOIN data_batches b ON b.id=r.batch_id
  WHERE r.dataset='instruments' AND b.status='published' AND r.batch_id<=%s
  ORDER BY r.code,r.batch_id DESC
) """


def numeric(value):
    try:
        number = Decimal(value)
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def summarize(conn, version):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    cutoff = now.date() if now >= ready_at(now.date().isoformat(), "d") else now.date() - timedelta(days=1)
    selection = BASIC + """, snapshots AS (
      SELECT s.day,s.codes,s.batch_id FROM universe_snapshots s WHERE s.batch_id=ANY(%s)
        AND EXISTS(SELECT 1 FROM daily_bar_index d WHERE d.day=s.day AND d.batch_id<=%s)
    ) SELECT s.*,coverage.expected FROM snapshots s
    CROSS JOIN LATERAL (
      SELECT count(*) FILTER(WHERE basic.payload->>'type' IN ('1','5')) AS expected,
        count(*) FILTER(WHERE basic.payload->>'type' IN ('1','5') AND d.source_id IS NOT NULL) AS present,
        count(*) FILTER(WHERE basic.code IS NULL OR basic.payload->>'type' NOT IN ('1','2','3','4','5') OR basic.payload->>'type' IS NULL) AS unknown
      FROM unnest(s.codes) c(code) LEFT JOIN basic ON basic.code=c.code
      LEFT JOIN daily_bar_index d ON d.day=s.day AND d.code=c.code AND d.batch_id<=%s
    ) coverage
    WHERE coverage.expected>0 AND coverage.expected=coverage.present AND coverage.unknown=0
      AND (SELECT count(*) FROM daily_bar_index d WHERE d.day=s.day AND d.code=ANY(%s) AND d.batch_id<=%s)=4
      AND (SELECT r.payload->>'is_trading_day' FROM source_records r JOIN data_batches b ON b.id=r.batch_id
           WHERE r.dataset='calendar' AND r.period=s.day::text AND b.status='published' AND r.batch_id<=%s
           ORDER BY r.batch_id DESC LIMIT 1)='1'
    ORDER BY s.day DESC LIMIT 1"""
    selected, cursor = None, cutoff + timedelta(days=1)
    while True:
        candidates = conn.execute("""SELECT s.day,max(s.batch_id) AS batch_id FROM universe_snapshots s JOIN data_batches b ON b.id=s.batch_id
          WHERE b.status='published' AND s.batch_id<=%s AND s.day<%s GROUP BY s.day ORDER BY s.day DESC LIMIT 16""",
          (version, cursor)).fetchall()
        if not candidates:
            break
        selected = conn.execute(selection, (version, [row["batch_id"] for row in candidates], version,
                                version, INDICES, version, version)).fetchone()
        if selected:
            break
        cursor = candidates[-1]["day"]
    note = "按已发布证券池、当前档案类型、原始日线及四个指数核对最近完整日；缺少证券池或日历的日期不宣称完整。涨跌按来源 pctChg，停牌不参与涨跌或排行，来源缺值单列。"
    if not selected:
        return {"status": "incomplete", "date": None, "version": version, "indices": [], "groups": {}, "note": note,
                "reason": "尚无能共同核验完整的股票/ETF、四个指数及交易日历；等待来源或回填。"}
    day = selected["day"]
    rows = conn.execute(BASIC + """SELECT basic.payload AS basic,r.payload AS quote
      FROM unnest(%s::text[]) c(code) JOIN basic ON basic.code=c.code
      JOIN daily_bar_index d ON d.code=c.code AND d.day=%s AND d.batch_id<=%s
      JOIN LATERAL (SELECT r.payload FROM source_records r
        WHERE r.id=d.source_id AND r.batch_id=d.batch_id AND r.dataset='bars:d' AND r.code=c.code AND r.period=%s LIMIT 1) r ON TRUE
      WHERE basic.payload->>'type' IN ('1','5') ORDER BY c.code""", (version, selected["codes"], day, version, str(day))).fetchall()
    indices = [row["payload"] for row in conn.execute("""SELECT r.payload FROM daily_bar_index d JOIN source_records r ON r.id=d.source_id AND r.batch_id=d.batch_id
      WHERE d.day=%s AND d.code=ANY(%s) AND d.batch_id<=%s AND r.dataset='bars:d' AND r.code=d.code AND r.period=%s ORDER BY d.code""",
      (day, INDICES, version, str(day))).fetchall()]
    if len(rows) != selected["expected"] or len(indices) != 4:
        return {"status": "incomplete", "date": None, "version": version, "indices": [], "groups": {}, "note": note,
                "reason": "日期索引与原始来源不一致，未发布统计或排名。"}
    groups = {}
    for kind, label in (("1", "A 股"), ("5", "ETF")):
        group = {"asset": label, "members": 0, "trading": 0, "paused": 0, "unknown_status": 0,
                 "up": 0, "down": 0, "flat": 0, "unknown_change": 0, "amount": Decimal(0), "missing_amount": 0}
        moves, amounts = [], []
        for row in rows:
            if row["basic"]["type"] != kind:
                continue
            group["members"] += 1
            basic, quote = row["basic"], row["quote"]
            if quote.get("tradestatus") == "0":
                group["paused"] += 1
                continue
            if quote.get("tradestatus") != "1":
                group["unknown_status"] += 1
                continue
            group["trading"] += 1
            item = {"code": basic["code"], "code_name": basic["code_name"], "type": kind,
                    "date": quote["date"], "close": quote["close"], "pctChg": quote.get("pctChg"), "amount": quote.get("amount")}
            change, amount = numeric(quote.get("pctChg")), numeric(quote.get("amount"))
            if change is None:
                group["unknown_change"] += 1
            else:
                group["up" if change > 0 else "down" if change < 0 else "flat"] += 1
                moves.append((change, item))
            if amount is None or amount < 0:
                group["missing_amount"] += 1
            else:
                group["amount"] += amount
                amounts.append((amount, item))
        group["gainers"] = [item for value, item in sorted(moves, key=lambda pair: (-pair[0], pair[1]["code"])) if value > 0][:10]
        group["losers"] = [item for value, item in sorted(moves, key=lambda pair: (pair[0], pair[1]["code"])) if value < 0][:10]
        group["turnover"] = [item for _, item in sorted(amounts, key=lambda pair: (-pair[0], pair[1]["code"]))][:10]
        group["amount"] = str(group["amount"])
        groups[kind] = group
    return {"status": "complete", "date": day, "version": version, "pool_version": selected["batch_id"],
            "indices": indices, "groups": groups, "note": note}
