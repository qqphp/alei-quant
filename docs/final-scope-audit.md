# 计划逐项验收索引（阶段0至5完成）

2026-10-11。依据 `development-plan.md` 阶段0至5及九项必要场景核对，当前本机版本在计划声明的范围内完成。分项证据保留各自日期、版本和资产窗口；最终33633版本的全量保存审计、当前程序文件及独立恢复另有完整证据。

## 阶段完成核对

| 原计划阶段 | 实际完成证据 | 结论及范围 |
|---|---|---|
| 0 环境、初始化、接口、规则与选型 | [本机环境](verification-local-delivery-state.json)、[接口矩阵](verification-interface-support.json)、[引擎选型](engine-decision.md)、[性能](performance-baselines.md)、[最终角色与运行状态](verification-final-runtime-readback.json) | 独立应用角色、迁移1至8、锁定依赖、本机监听；26方法接入，文档声明与实际来源分开 |
| 1 首个日线流程 | [独立账本](verification-ledger.json)、[当前570日报告](verification-current-long-classic-performance.json)、[最终归档重算](verification-final-source-restore.json) | 行情、双均线、下一开盘、费用/T+1、权益/现金/净值及固定版本可重算；经典示例启用 |
| 2 日线平台与模型 | [全部原请求回执](verification-collection-operation-coverage.json)、[模型生命周期](verification-current-model-lifecycle.json)、[取消](verification-cancel.json)、[筛选导出](verification-filter-export.json)、[历史分页恢复](verification-latest-delivery-restore.json) | 原31346项及额外878项全部完成；本地安装/启停/参数/版本、行情/自选/筛选/详情/对比/导出可用 |
| 3 基本面与低频接口 | [八族日期读取](verification-financial-families.json)、[修订隔离](verification-financial-revision.json)、[五类宏观](verification-macro-support.json)、[全部源值恢复](verification-final-source-restore.json) | 六类财务、快报/预告、行业/成分、五类宏观已接入及展示；回溯历史与发布日期限制可见 |
| 4 多周期、分钟及ETF | [官方周期](verification-periods.json)、[周期账本](verification-period-backtests.json)、[七频率历史池输入](verification-universe-input-all-frequencies.json)、[两板回归](verification-growth-final-regressions.json)、[ETF账本](verification-etf-ledger.json) | 日/周/月和5/15/30/60分钟、有限资产/日期ETF窗口完成；源冲突与未知规则拒绝，不宣称全市场分钟已下载 |
| 5 使用、性能、研究与恢复 | [当前参数研究](verification-current-parameter-research.json)、[调度补跑](verification-scheduler-catchup.json)、[启动](verification-startup-readiness.json)、[全部交付恢复](verification-latest-delivery-restore.json)、[最终源值恢复](verification-final-source-restore.json)、[最终服务查询](verification-final-runtime-readback.json) | 中文说明、启动/停止、离线水位补跑、具体本机性能、参数锁定、独立备份恢复及当前报告重算完成 |

## 已有交付及证据边界

| 计划场景 | 已核对证据 | 适用限制／最终工作 |
|---|---|---|
| 接口、字段、用途及状态 | [26方法矩阵](interface-support.md)、[实际统计](verification-interface-support.json)；六类财务及快报/预告[日期读取](verification-financial-families.json) | 方法已接入不等于所有证券/年份已入库；空返回不证明无权益，部分财报没有首次发布版本 |
| 分页、重复、失败及续跑 | [主任务逐操作回执](verification-collection-operation-coverage.json)、[重复分红与原检查点恢复](mindray-dividends.md)、[历史任务/日志](verification-job-log-pagination.json) | 原31346项及额外878项已完成，固定33633版本每项原请求均有发布回执；失败原证据继续保留 |
| 上市/退市、名称、停牌、缺口 | [910日历史池](verification-universe-input-full-range-0121.json)、[真实价格缺口分类](daily-member-coverage.md)、[模型池输入](historical-universe-input.md) | 最终33633版本4652019源成员日期仅1项精确非价格标记无价格；来源回溯成员不能证明当时实际可交易，300114→302132跨代码连续持仓未支持 |
| 数量/现金、T+1、费用及成交 | [初始逐日手算](verification-ledger.json)、[创业板实际账本](verification-growth-ledger.json)、[分钟账本](verification-growth-minute-ledger.json)、[当前长区间结果](verification-current-long-classic-performance.json) | 费用/板块规则有明确历史窗口；成交仍是OHLC近似，真实排队不可重建；旧引擎证据保留其原版本 |
| ETF分类、精度与权益 | [ETF账本](verification-etf-ledger.json)、[归档重算](verification-etf-replays.json) | 有限已验证资产/日期；股票规则不直接复用于未知产品，不能扩大为全部ETF覆盖 |
| 分红、送转、复权及到账 | [真实转增](verification-bonus-ledger.json)、[已发布源完整摘要](action-source-integrity.md)、[迈瑞两次共同执行](mindray-dividends.md) | 特定公告补充仅适用于精确事件；003001调整公告时钟/零碎结算未验证，仍拒绝账本；未知碎股、股票股利税额等不能泛化 |
| 财报/快报修订、宏观时钟 | [财务修订](verification-financial-revision.json)、[八族日期过滤](verification-financial-families.json)、[五类宏观609行](macro-data.md) | 固定本地版本及保守公告时钟不证明首次发布真值；宏观仅浏览/导出，模型声明拒绝 |
| 周/月/分钟、预热与模型输入 | [源周期校验](verification-periods.json)、[周期回测](verification-period-backtests.json)、[七频率模型池实际回调](verification-universe-input-all-frequencies.json) | 仅各实际区间/资产，足够预热单独校验；不能把源冲突窗口或未完成周期降级成有效行情 |
| 更新、取消、不可变引用及恢复 | [实际进程取消](verification-cancel.json)、[模型含缓存安装](verification-bytecode-package-installation.json)、[启动续跑](verification-startup-readiness.json)、[最新交付恢复](verification-latest-delivery-restore.json)、[最终全量恢复](verification-final-source-restore.json) | 14:39备份及独立库已完成恢复，固定33633全部源记录/批次/池快照所有列相等；首笔0.1.0报告仅阅读/导出，无完整引擎归档，不支持原引擎重算 |
| 指标、基准及参数研究 | [周期净值/指标](verification-period-backtests.json)、[当前五任务研究](verification-current-parameter-research.json)、[570日结果对照](verification-current-long-classic-performance.json) | 基准为价格收益，非股息总收益；研究各段独立资金，已查看历史不宣称真正未触碰样本外 |
| 中文界面、历史访问及资源 | [市场总览](market-summary.md)、[筛选导出](market-filter-export.md)、[估值](valuation-charts.md)、[历史报告分页](verification-run-history-pagination.json)、[本机性能](performance-baselines.md) | 资源测量为本机具体单次范围，不能外推全市场分钟或并发容量；前端大包警告保留 |

## 完成门槛核对

1. 原全市场任务31346项、额外历史成员878项已完成，原任务摘要保持不变；本项已实际核对。
2. 最终33633版本的910日源A股成员价格缺口、所有因子/分红原值摘要、每项请求回执及经济合并限制已核对；全部10276676条发布原行字段/摘要及独立数据集计数审计已通过。实际无价格、空响应、来源不支持与仅采集事件保留，不能称全市场无缺口。
3. 最终备份 `runtime/backups/20261011-143926-574371` 已完成，1201个复制文件摘要核对；独立库 `stock_alei_restore_20261011_144224_4c9165` 实际恢复。42模型状态、9报告、参数研究及全部历史分页相等；三项交付报告加570日/迈瑞两账本共六项补充完整重算相等。全部10276676源记录、33633批次、913池快照所有列摘要相等，277份说明/原件文件摘要核对。
4. 本索引、实施记录和中文说明已按最终33633结果更新。恢复后生成的证据与完成说明作为快照后附件保存，不声称它们存在于先前dump中。当前服务/工作进程存活、应用角色非管理员，八项代表性HTTP读取成功，经典模型保持启用。

四项门槛已实际核对通过，原计划阶段0至5在上述声明范围内完成。首次发布真值、OHLC真实排队、未核验特殊行动、全市场分钟及任意外部模型兼容性仍是明确限制，不能由本次验收扩大。


全体发布原行已在最终固定33633版本实际核对10276676条、32数据集，26个SDK方法与1行正式公告补充分开计数，耗时537.25秒，见 [完整原行摘要](verification-all-source-row-integrity.json)。四项共同版本审计均成功退出且内容读回通过；最终原值恢复及六项补充报告重算也通过。执行顺序及备份文件摘要清单见 [最终恢复步骤](final-restore-procedure.md)。


失败响应已补强完整已取得解析行与未解析值保留，完整协议标记和覆盖状态分开；两个明确恢复副本模拟场景未发布任何源记录，见 [失败响应保留](verification-failed-source-response.json)。旧失败批次原先只保留冲突子集的范围继续标明；没有伪造恢复其未保存响应。


## 江波龙共同实施分红的精度与结算边界

江波龙301308的2026-06-02共同实施分红已按正式公告核对：SDK现金字段合计0.990744，官方每股0.9907442且现金分以下舍尾。仅对两个完整原行摘要增加采集身份，批次32385真实接收/保存2行，与失败32384完整响应相等；16项内存未知条款拒绝，完整/缺组件/重复四种账本输入均拒绝。见 [正式条款](research/jiangbolong-2026-joint-dividend.md)与 [精度及原值核对](verification-jiangbolong-source-precision.json)。不改写源金额、不开放该行动回测；原任务从30097检查点恢复并推进30134，摘要不变。本轮程序/公告/说明仍须包含于最终备份。来源代码301308为江波龙，早先状态提示误称中际旭创已更正。


## 原全市场任务完成及最终同版审计记录

2026-10-11 14:29:06，原任务31346/31346完成，额外历史成员任务878/878完成，原操作摘要保持不变。固定发布版本33633：全部原请求均有发布回执；17705因子/16961分红修订按原字段顺序重建摘要一致，4347个分红代码中仅003001和301308的已知特殊事件继续拒绝账本。910日4652019个源A股成员日期中4652018项有价格，唯一无价格项为300114在2025-02-17的精确非价格标记，未补造价格。详见同版 `verification-collection-operation-coverage.json`、`verification-action-source-integrity.json`、`verification-daily-member-coverage.json`。后续全部发布源行审计、最终备份与独立恢复均已完成，完整证据见上表；未把请求成功或存在价格推导成历史首次发布真值、全部权益完整或全证券可回测。审计及恢复工具已纳入备份scripts目录。
