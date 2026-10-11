"""已核验的代码变更与非价格标记；不合并行情或转换回测持仓。"""
from hashlib import sha256

from . import db
from .config import ROOT


REFERENCE = {
    "code": "sz.300114", "successor": "sz.302132", "effective_date": "2025-02-17",
    "source_url": "https://static.cninfo.com.cn/finalpage/2025-02-15/1222544408.PDF",
    "announcement": "2025-028", "document": "docs/research/300114-code-change-20250217.pdf",
    "sha256": "dd68049c48df826848f361fd9e7b23dd20b6805144a2e5bc36e54db638611488",
    "note": "公司上市主体存续、代码变更；来源旧代码停用标记没有价格。尚不支持跨代码连续行情或回测持仓转换。",
}
MARKER = {
    "date": "2025-02-17", "code": "sz.300114", "open": "", "high": "", "low": "", "close": "",
    "preclose": "", "volume": "", "amount": "", "turn": "", "pctChg": "", "adjustflag": "3",
    "tradestatus": "0", "isST": "0", "peTTM": "0.000000", "pbMRQ": "0.000000",
    "psTTM": "0.000000", "pcfNcfTTM": "0.000000",
}


def marker_reference(dataset, row):
    # 精确匹配已收到的完整原始行；别的空价格、日期、字段或复权标记仍校验失败。
    if dataset != "bars:d" or row != MARKER:
        return None
    document = ROOT / REFERENCE["document"]
    if not document.is_file() or sha256(document.read_bytes()).hexdigest() != REFERENCE["sha256"]:
        raise ValueError("代码变更公告原件缺失或摘要不一致，未接受非价格标记")
    return {"raw": dict(row), "reference": dict(REFERENCE)}


def published_markers(code, start, end, version):
    return db.rows("SELECT DISTINCT ON(marker->'raw'->>'date') id AS source_batch,marker "
                   "FROM data_batches CROSS JOIN LATERAL jsonb_array_elements("
                   "COALESCE(coverage->'non_price_markers','[]'::jsonb)) marker "
                   "WHERE status='published' AND id<=%s AND arguments->>'code'=%s "
                   "AND marker->'raw'->>'date' BETWEEN %s AND %s "
                   "ORDER BY marker->'raw'->>'date',id DESC", (version,code,start,end))
