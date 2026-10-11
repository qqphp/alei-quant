"""方法采集证据与数据集用途分开；样本发布不是全历史覆盖证明。"""
from .macro import MACRO_POLICIES
from .plugins import FINANCIAL_DATASETS


def capabilities(endpoint, collection, stats):
    dataset = endpoint["dataset"]
    datasets = sorted(name for name in stats if name.startswith("bars:")) if dataset == "bars" else [dataset]
    coverage = [stats[name] for name in datasets if name in stats]
    received = (collection or {}).get("nonempty_batches", 0)
    collected = (collection or {}).get("published_batches", 0)
    collection_status = "verified" if received else "partial" if collected else "pending"
    if dataset in FINANCIAL_DATASETS:
        access = "声明后按公告时钟读取"
        scope = "八类财务支持固定版本及公告日后首个交易日过滤；快报取公告/更新较晚日期。真实多族上下文和盈利模型已核验，当前回溯值不保证首次发布原版，字段/证券/报告期缺失仍拒绝。"
        unsupported, pending = [], ["首次发布原版及全部历史修订"]
    elif dataset == "bars" or dataset.startswith("bars:"):
        access = "行情窗口 / 引擎撮合与基准"
        scope = "日/周/月及四种分钟频率已有真实样本；主板 2023 年起、两板 2023-01-01 至 2026-10-09、510050/510300 的已核验 2026 窗口按独立规则开放。指数只作基准，完整源窗口/预热和公司行动仍逐运行检查；官方复权参考不用于成交。"
        unsupported = ["指数分钟线", "周/月及分钟估值字段", "实时 / 逐笔 / 盘口"]
        pending = ["其它 ETF 产品规则与权益", "全市场所有频率完整历史", "未核验的分钟来源冲突"]
        if endpoint["method"] == "query_daily_history_k_AStock":
            scope = "本方法只采集全市场 A 股日线；其它周期由历史 K 线方法采集。" + scope
        elif endpoint["method"] == "query_daily_history_k_ETF":
            scope = "本方法只采集全市场 ETF 日线；其它周期由历史 K 线方法采集。" + scope
    elif dataset in {"factors", "dividends"}:
        access = "引擎处理；不直接传入策略"
        scope = "因子用于展示与公司行动核对，权益按登记/除权/到账/上市分别处理；已有股票/ETF/分红组成及送转真实账本。未知组成、零碎结算或必要日期缺失仍拒绝，空返回不证明无权益。"
        unsupported = []
        pending = ["所有资产与年份的公司行动完整覆盖", "未核验特殊权益及零碎结算"]
    elif dataset == "calendar":
        access = "平台交易日与就绪校验"
        scope = "已入库交易日历用于历史区间、权益、周期信号和完整日检查；未来日历记录不是未来行情，未发布或必要日期缺失不补造。"
        unsupported, pending = [], ["已验证日期区间以外的历史覆盖"]
    elif dataset == "universe":
        access = "声明后按信号时钟读取 / 查询 / 回填"
        scope = "查询日期、完整成员和源交易状态版本化保存；声明 universe 后按信号前已就绪的完整快照提供代码/交易状态，分钟不读取当天17:30才就绪的池。可按逐日成员并集回填，已有2023-01-01至2026-10-09的910日来源快照。实际新代码在正式启用前已被来源回溯返回；不是当时可交易/首次发布的证明。名称、当前类型与ST不进入模型；空名称和相同重复显式处理，行情仍独立校验。"
        unsupported, pending = [], ["历史当年名称及分类生效版本", "已核验区间之外的成员覆盖", "全区间成员行情覆盖"]
    elif dataset == "instruments":
        access = "当前档案 / 资产规则核对"
        scope = "搜索、当前上市/退市状态、类型及上市日期；当前状态不能当作历史可交易状态。档案有其它/可转债类型不证明该类行情或回测可用。"
        unsupported, pending = [], ["历史档案生效版本", "其它资产行情及回测支持"]
    elif dataset in {"industry", "sz50", "hs300", "zz500"}:
        access = "浏览 / 当前参考筛选"
        scope = "保留来源 updateDate 与成员/行业原值；已接入浏览、导出和当前行业筛选。更新日未证明是历史真实生效日，不直接声明为策略输入。"
        unsupported, pending = [], ["任意历史时点的真实行业/成分生效版本"]
    elif dataset in MACRO_POLICIES:
        access = "仅浏览 / 导出"
        scope = "五类宏观全部原值和字段覆盖已核对；政策日期语义、陈旧序列及货币修订限制明确。模型不接受宏观声明，不用统计期或抓取日制造历史时钟。"
        unsupported = ["月/年货币量的发布日期字段"] if dataset.startswith("money_") else []
        pending = ["完整政策序列", "原始历史发布与修订版本"]
    else:
        raise ValueError("目录数据族缺少用途映射")
    return {"collection_status": collection_status,
            "collection_label": {"verified": "采集已有非空发布", "partial": "仅有成功空返回", "pending": "待采集验证"}[collection_status],
            "collection_evidence": collection or {"published_batches": 0, "nonempty_batches": 0, "received_rows": 0},
            "coverage_status": "partial" if any(row["revisions"] for row in coverage) else "pending",
            "coverage_label": "部分覆盖" if any(row["revisions"] for row in coverage) else "待验证",
            "source_coverage": coverage, "strategy_access": access, "scope": scope,
            "source_unsupported": unsupported, "pending_verification": pending}
