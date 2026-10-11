"""保留源方案阶段；账本合并同一行动的相容记录，冲突不猜测。"""
from decimal import Decimal, InvalidOperation
import hashlib

from . import db
from .config import ROOT
from .source_revisions import ZHONGYAN_LIMIT, JIANGBOLONG_LIMIT

NUMERIC_TERMS = ("dividCashPsBeforeTax", "dividStocksPs", "dividReserveToStockPs")
DATE_TERMS = ("dividPayDate", "dividStockMarketDate")

# 仅区分已核验的源公告版本，不把更正记录当作第二项分红权益。
VERIFIED_SOURCE_REVISIONS = [{
    "fields": {"code": "sh.688320", "dividOperateDate": "2024-06-14", "dividRegistDate": "2024-06-13",
               "dividPayDate": "2024-06-14", "dividPlanAnnounceDate": "2024-04-26",
               "dividPlanDate": "2024-06-07", "dividAgmPumDate": "2024-05-17"},
    "stages": {"0.11": {"label": "original_20240607", "after_tax": "0.099或0.11",
                         "document": "docs/research/hechuan-2024-distribution-original.pdf",
                         "source": "https://static.cninfo.com.cn/finalpage/2024-06-07/1220289873.PDF",
                         "sha256": "06fa22c5ffc2fee854cb2b88fc827395f6834a15055a1d43857b3293d0b86bc0"},
               "0.11038": {"label": "corrected_20240608", "after_tax": "0.09934或0.11038",
                            "document": "docs/research/hechuan-2024-distribution-corrected.pdf",
                            "source": "https://static.cninfo.com.cn/finalpage/2024-06-08/1220299514.PDF",
                            "sha256": "83810793588a526467d8925e75a6d9cd7e770b55e376053bb31c9c9529f0ca2f"}},
}]


def verified_revision(row):
    """按完整源日期和已核验条款识别版本；经济权益仍由账本单独校验。"""
    for rule in VERIFIED_SOURCE_REVISIONS:
        if any(row.get(field) != value for field, value in rule["fields"].items()):
            continue
        try:
            if any(Decimal(row.get(field) or "0") != 0 for field in ("dividStocksPs", "dividReserveToStockPs")):
                continue
            cash = Decimal(row.get("dividCashPsBeforeTax") or "0")
        except InvalidOperation:
            continue
        if row.get("dividStockMarketDate") or row.get("dividPreNoticeDate"):
            continue
        for amount, stage in rule["stages"].items():
            if cash == Decimal(amount) and row.get("dividCashPsAfterTax") == stage["after_tax"]:
                return stage["label"]
    return None

# BaoStock 未提供差异化除息的摊薄金额；仅采用逐项核对的实施公告，不由价格反推。
VERIFIED_REFERENCES = [{
    "code": "sh.688320", "ex_date": "2024-06-14", "record_date": "2024-06-13", "pay_date": "2024-06-14",
    "announcement_date": "2024-06-08", "original_announcement_date": "2024-06-07",
    "cash_per_share": "0.11038", "reference_cash_per_share": "0.10990",
    "participating_shares": 150362477, "total_shares": 151013668, "bonus_ratio": "0",
    "selected_revision": "corrected_20240608",
    "document": "docs/research/hechuan-2024-distribution-corrected.pdf",
    "source": "https://static.cninfo.com.cn/finalpage/2024-06-08/1220299514.PDF",
    "sha256": "83810793588a526467d8925e75a6d9cd7e770b55e376053bb31c9c9529f0ca2f",
}, {
    "code": "sh.688503", "ex_date": "2026-06-18", "record_date": "2026-06-17", "pay_date": "2026-06-18",
    "announcement_date": "2026-06-12", "cash_per_share": "0.432", "reference_cash_per_share": "0.413",
    "participating_shares": 231483309, "total_shares": 242033643, "bonus_ratio": "0",
    "document": "docs/research/juhe-2026-dividend-original.pdf",
    "source": "https://static.cninfo.com.cn/finalpage/2026-06-12/1225365375.PDF",
    "sha256": "889777c27aac4dc608559254b1f7d8e7d55506acaf63bac944574a85b8501715",
}, {
    "code": "sh.600519", "ex_date": "2025-06-26", "record_date": "2025-06-25", "pay_date": "2025-06-26",
    "announcement_date": "2025-06-20", "cash_per_share": "27.673", "reference_cash_per_share": "27.600",
    "participating_shares": 1252887715, "total_shares": 1256197800, "bonus_ratio": "0",
    "source": "https://www.moutaichina.com/mtgf/attachDir/2025/06/2025062718293551833.pdf",
    "sha256": "8ff3e6e370c75cece700e5474337c8ecf1adb87b01a1a696bae02fd25982af3b",
}, {
    "code": "sh.601966", "ex_date": "2024-06-14", "record_date": "2024-06-13", "pay_date": "2024-06-14",
    "announcement_date": "2024-06-07", "plan_announce_date": "2024-04-25", "implementation_date": "2024-06-07",
    "cash_per_share": "0.377", "reference_cash_per_share": "0.37444",
    "cash_components": {"0.286": "2023年度", "0.091": "2024年一季度"},
    "participating_shares": 1463502683, "total_shares": 1473522713, "bonus_ratio": "0",
    "source": "http://epaper.oss.zqrb.cn/images/2024-06/07/D29/zqrb20240607D29.pdf",
    "sha256": "23fe5832f3338bbb24f313445f0ac95cddb5b3bc007c102b3bfa115b57f01c8e",
}, {
    "code": "sz.002043", "ex_date": "2026-06-01", "record_date": "2026-05-29", "pay_date": "2026-06-01",
    "announcement_date": "2026-05-26", "plan_announce_date": "2026-04-29", "implementation_date": "2026-05-26",
    "cash_per_share": "0.312", "reference_cash_per_share": "0.3080016",
    "cash_components": {"0.132": "2025年度", "0.18": "2026年一季度"},
    "participating_shares": 819121119, "total_shares": 829754480, "bonus_ratio": "0",
    "document": "docs/research/tubao-2026-joint-dividend.pdf",
    "source": "https://static.cninfo.com.cn/finalpage/2026-05-26/1225328690.PDF",
    "sha256": "56119c4f332f7ae733bff068a16fd290281efa559006ba2547faafe2bbf21e70",
}, {
    "code": "sz.300760", "ex_date": "2025-05-29", "record_date": "2025-05-28", "pay_date": "2025-05-29",
    "announcement_date": "2025-05-22", "plan_announce_date": "2025-04-29", "implementation_date": "2025-05-22",
    "cash_per_share": "1.97", "reference_cash_per_share": "1.97",
    "cash_components": {"0.56": "2024年度", "1.41": "2025年第一次中期"},
    "source_fields": {"dividAgmPumDate": "2025-05-20", "dividPreNoticeDate": "", "dividStockMarketDate": ""},
    "participating_shares": 1212441394, "total_shares": 1212441394, "bonus_ratio": "0",
    "document": "docs/research/mindray-2025-joint-dividend.pdf",
    "source": "https://static.cninfo.com.cn/finalpage/2025-05-22/1223626414.PDF",
    "sha256": "98470e8000f0d2c45c4b2584be0e92bb2741668cdba0a358858f8120c37d4fb9",
}, {
    "code": "sz.300760", "ex_date": "2026-05-28", "record_date": "2026-05-27", "pay_date": "2026-05-28",
    "announcement_date": "2026-05-20", "implementation_date": "2026-05-20",
    "component_plan_announce_dates": {"0.31": "2026-03-31", "1.25": "2026-04-29"},
    "cash_per_share": "1.56", "reference_cash_per_share": "1.56",
    "cash_components": {"0.31": "2025年度", "1.25": "2026年第一次中期"},
    "source_fields": {"dividAgmPumDate": "2026-05-19", "dividPreNoticeDate": "", "dividStockMarketDate": ""},
    "participating_shares": 1212441394, "total_shares": 1212441394, "bonus_ratio": "0",
    "document": "docs/research/mindray-2026-joint-dividend.pdf",
    "source": "https://static.cninfo.com.cn/finalpage/2026-05-20/1225320390.PDF",
    "sha256": "c13e4f6fd029b359dfb349c7d370fcaa9a4bc3a59791a17c24c07f4539267457",
}]


def verify_reference_documents(codes):
    documents = [rule for rule in VERIFIED_REFERENCES if rule["code"] in codes and rule.get("document")]
    documents += [stage for rule in VERIFIED_SOURCE_REVISIONS if rule["fields"]["code"] in codes for stage in rule["stages"].values()]
    for rule in documents:
        raw = (ROOT / rule["document"]).read_bytes()
        if not raw.startswith(b"%PDF") or hashlib.sha256(raw).hexdigest() != rule["sha256"]:
            raise ValueError(f"正式分红公告原件摘要变化：{rule['document']}")


def verified_component(row):
    """来源没有报告期字段；仅用核验公告的完整日期/比例识别同日不同分红。"""
    for rule in VERIFIED_REFERENCES:
        if not rule.get("cash_components"):
            continue
        fields = {"code": "code", "dividOperateDate": "ex_date", "dividRegistDate": "record_date",
                  "dividPayDate": "pay_date", "dividPlanDate": "implementation_date"}
        if any(row.get(field) != rule[target] for field, target in fields.items()):
            continue
        if any(row.get(field) != value for field, value in rule.get("source_fields", {}).items()):
            continue
        try:
            cash = Decimal(row.get("dividCashPsBeforeTax") or "0")
            bonus = sum((Decimal(row.get(f) or "0") for f in ("dividStocksPs", "dividReserveToStockPs")), Decimal(0))
        except InvalidOperation:
            continue
        if bonus != Decimal(rule["bonus_ratio"]):
            continue
        for amount, label in rule["cash_components"].items():
            plan_date = rule.get("component_plan_announce_dates", {}).get(amount, rule.get("plan_announce_date"))
            if cash == Decimal(amount) and row.get("dividPlanAnnounceDate") == plan_date:
                return label
    return None


def reference_cash(action):
    cash = Decimal(action.get("dividCashPsBeforeTax") or "0")
    rule = next((r for r in VERIFIED_REFERENCES if (r["code"], r["ex_date"]) ==
                 (action["code"], action.get("dividOperateDate"))), None)
    if rule:
        bonus = sum((Decimal(action.get(f) or "0") for f in ("dividStocksPs", "dividReserveToStockPs")), Decimal(0))
        if (cash != Decimal(rule["cash_per_share"]) or bonus != Decimal(rule["bonus_ratio"])
                or action.get("dividRegistDate") != rule["record_date"] or action.get("dividPayDate") != rule["pay_date"]
                or rule["announcement_date"] >= rule["ex_date"]):
            raise ValueError(f"{action['code']} 差异化分红与已核验实施公告不相容，停止而不覆盖来源")
        return Decimal(rule["reference_cash_per_share"]), rule
    return cash, None


def merge_dividends(rows):
    groups = {}
    for row in rows:
        if row.get('code') == 'sz.301308' and row.get('dividOperateDate') == '2026-06-02':
            raise ValueError(JIANGBOLONG_LIMIT)
        if row.get('code') == 'sz.003001' and row.get('dividOperateDate') == '2025-07-07':
            raise ValueError(ZHONGYAN_LIMIT)
        key = tuple(row.get(field) or "" for field in ("code", "dividOperateDate", "dividRegistDate", "dividPlanAnnounceDate")) + (verified_component(row) or "",)
        groups.setdefault(key, {})[db.dumps(row)] = row
    actions = []
    for key, variants in sorted(groups.items()):
        records = list(variants.values())
        source_records = records
        revisions = [verified_revision(row) for row in records]
        selected_revision = None
        if any(revisions):
            if not all(revisions) or "corrected_20240608" not in revisions:
                raise ValueError(f"{key[0]} {key[1]} 缺少已核验更正版本或出现未知版本，停止账本")
            # 只有逐字段匹配正式公告的这一事件可择更正版；不是通用最新金额优先。
            records = [row for row in records if verified_revision(row) == "corrected_20240608"]
            selected_revision = "corrected_20240608"
        action = dict(max(records, key=lambda row: (row.get("dividPlanDate") or "", sum(v not in (None, "") for v in row.values()), db.dumps(row))))
        for field in NUMERIC_TERMS + DATE_TERMS:
            present = [r[field] for r in records if r.get(field) not in (None, "")]
            try:
                values = {Decimal(v) for v in present} if field in NUMERIC_TERMS else set(present)
                if field in NUMERIC_TERMS and any(not v.is_finite() or v < 0 for v in values):
                    raise ValueError("比例或现金必须有限且非负")
            except InvalidOperation as exc:
                raise ValueError(f"{key[0]} {key[1]} 的 {field} 不是有效数值") from exc
            if len(values) > 1:
                raise ValueError(f"{key[0]} {key[1]} 分红方案阶段的 {field} 冲突，停止账本而不重复派息")
            if present and action.get(field) in (None, ""):
                action[field] = present[0]
        action["source_variants"] = len(source_records)
        if len(source_records) > 1 and not selected_revision:
            action["source_records"] = source_records
        if selected_revision:
            action.update(verified_revision=selected_revision, verified_revision_records=source_records,
                          verified_announcement_date="2024-06-08")
        actions.append(action)
    for rule in VERIFIED_REFERENCES:
        if not rule.get("cash_components"):
            continue
        components = [a for a in actions if a["code"] == rule["code"] and a.get("dividOperateDate") == rule["ex_date"]]
        if not components:
            continue
        labels = [verified_component(a) for a in components]
        if sorted(str(v) for v in labels) != sorted(rule["cash_components"].values()):
            raise ValueError(f"{rule['code']} {rule['ex_date']} 同日合并分红的公告组成不完整或条款变化，停止账本")
        combined = dict(components[0])
        combined.update(dividCashPsBeforeTax=str(sum((Decimal(a["dividCashPsBeforeTax"]) for a in components), Decimal(0))),
                        dividCashPsAfterTax="", dividCashStock="核验公告：同日多个方案合并分配",
                        source_variants=sum(a["source_variants"] for a in components), verified_components=components)
        actions = [a for a in actions if a not in components] + [combined]
    actions.sort(key=lambda a: (a["code"], a.get("dividOperateDate") or "", a.get("dividPlanAnnounceDate") or ""))
    return actions
