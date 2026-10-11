# BaoStock 数据能力与回测注意事项

核对及审阅修订日期：2026-10-10。本文记录方案审阅时依据官网文档、访问规则与官方 PyPI 发布包的静态核验；实施后的真实查询、回测及覆盖证据另见 [实施记录](../implementation-status.md)。官网正文通过 POST `/helpdocs/api/markdown/<文件名>` 加载，菜单通过 POST `/helpdocs/api/menu` 提供，文中链接指向对应官方页面。

## 对平台方案的结论

BaoStock 可作为本地平台的中国股票历史行情和基本面数据源，适合经 Python 客户端拉取后落入 PostgreSQL。它提供的是历史及收盘后更新的数据接口；官方资料没有把它描述为实时行情、逐笔成交或交易执行服务。因此第一阶段宜围绕收盘行情、证券查询和日/季频回测设计。([平台介绍](https://www.baostock.com/mainContent?file=home.md)、[Python API 文档](https://www.baostock.com/mainContent?file=pythonAPI.md))

## 可用接口与数据范围

| 数据族 | 官方接口与适用范围 |
| --- | --- |
| K 线与估值 | `query_history_k_data_plus()` 支持日、周、月及 5/15/30/60 分钟线；日线可含 OHLC、昨收、成交量/额、换手、停牌状态、涨跌幅、ST 标记及 PE/PB/PS/PCF。分钟字段更少，指数没有分钟线，估值只提供 A 股日频。([K 线接口](https://www.baostock.com/mainContent?file=stockKData.md)、[日频估值](https://www.baostock.com/mainContent?file=valuationDaily.md)) |
| 股票、指数、ETF | 官网列明股票日/周/月线从 1990-12-19 起，分钟线标注从 2020-01-03 起；指数日/周/月线从 2006-01-01 起且无分钟线；ETF 日/周/月及分钟线从 2026-01-05 起。分钟线“近 5 年”的标签与其所列起始日期在 2026-10-10 不一致，部署前应对计划回测的代码和区间实查。([平台介绍](https://www.baostock.com/mainContent?file=home.md)、[指数数据](https://www.baostock.com/mainContent?file=indexData.md)) |
| 证券资料与交易日历 | `query_stock_basic()` 返回代码、名称、上市/退市日期、类型和状态（类型含股票、指数、其它、可转债、ETF，但元数据存在不代表对应行情可用）；`query_all_stock(day=...)` 可按历史日期取沪深证券及交易状态，当天闭市后数据就绪前可能返回空；`query_trade_dates()` 提供交易日历。各接口历史起点须实查，不能由日线覆盖起点推断。([证券基本资料](https://www.baostock.com/mainContent?file=stockBasic.md)、[证券元信息](https://www.baostock.com/mainContent?file=StockBasicInfoAPI.md)) |
| 复权与公司行动 | `query_adjust_factor()` 提供复权因子，`query_dividend_data()` 提供除权除息信息；官网称其复权算法为“涨跌幅复权法”，并提醒与其他系统的复权结果可能不同。([复权因子](https://www.baostock.com/mainContent?file=factorInfo.md)、[除权除息](https://www.baostock.com/mainContent?file=dividInfo.md)、[本地复权计算说明](https://www.baostock.com/mainContent?file=localdatafactorInfo.md)) |
| 财务与报告 | 季频接口覆盖盈利、营运、成长、偿债、现金流和杜邦指标；另有业绩快报、业绩预告。官网列明季频财务从 2007 年起（首页同时说明实际包含的是“部分上市公司”相关报表数据，不能默认全市场、全字段完整）、业绩预告从 2003 年起、业绩快报从 2006 年起。([Python API 文档](https://www.baostock.com/mainContent?file=pythonAPI.md)、[平台介绍](https://www.baostock.com/mainContent?file=home.md)) |
| 指数成分与宏观 | 提供上证 50、沪深 300、中证 500 成分股接口；宏观接口包括存款/贷款利率、准备金率、月度货币供应量和年度余额。([Python API 文档](https://www.baostock.com/mainContent?file=pythonAPI.md)) |
| 每日批量更新 | `query_daily_history_k_AStock()`、`query_daily_history_k_ETF()`、`query_daily_adjust_factor()` 分别按日期取全市场 A 股日线、ETF 日线及当日复权因子；新接口的历史起点需实测。停牌日 OHLC 等于前收盘价，成交量/额为 0，换手可为空。ETF 样例估值为空，且含 `isST=1`，应按资产类型核验字段适用性，不能直接套股票语义。([每日更新 API](https://www.baostock.com/mainContent?file=DailyUpdates.md)) |

官网给出的入库时刻为交易日 17:30（日线）、18:00（复权因子）、20:00（分钟线），其它财务报告为交易日的次一自然日 01:30；周线为周六 17:30，月线为每月 1 日 17:30 完成上月数据。三种指数成分每周一下午更新，行业分类每周一更新。调度应检查真实就绪状态，并区分官方完整周期线与本地派生的周期内行情。([平台介绍](https://www.baostock.com/mainContent?file=home.md)、[行业分类](https://www.baostock.com/mainContent?file=stockIndustry.md))

## 客户端与本地入库

官方 Python 客户端通过 `pip install baostock` 安装，文档要求 Python 3.6 或 Python 3.9 及以上并依赖 pandas。典型调用先 `login()`，检查 `error_code` / `error_msg`，再通过 `QueryResult.next()` 和 `get_row_data()` 迭代结果，最后 `logout()`；`.get_data()` 可取 DataFrame。客户端返回的是 pandas 数据，不是 PostgreSQL 写入器，因此平台需要独立的同步任务负责类型转换、幂等写入、错误记录和断点续跑。([平台介绍](https://www.baostock.com/mainContent?file=home.md)、[Python API 文档](https://www.baostock.com/mainContent?file=pythonAPI.md)、[每日更新 API](https://www.baostock.com/mainContent?file=DailyUpdates.md))

截至官网 0.9.4 版本说明，客户端支持可选 `API_KEY` 并可访问 `vip-api.baostock.com`；默认服务由已发布的 Python 包配置为 `public-api.baostock.com:10030`。同步进程仍依赖外部服务和网络；当前资料未给出服务可用性 SLA。节点/API_KEY 可选不等于获准并发或配额豁免。([官网版本说明](https://www.baostock.com/mainContent?file=home.md)、[BaoStock 0.9.4 发布包](https://files.pythonhosted.org/packages/66/61/8ad575eb83d4b22852a922e669bc13c9723675343e885b0040ab11a6a321/baostock-0.9.4.tar.gz))

**本次审阅补正：初稿只查首页，漏查了访问规则。** 官网规则明确每天 API 请求不能超过 50,000 次，并禁止并发连接；本自然年度首次受限冻结 6 小时，后续冻结时长按本年限制次数乘 6 小时计算。平台应共享请求预算、保留余量并串行采集，不能照搬“无限流”或多进程并发回填的旧说法。([官方访问规则](https://www.baostock.com/blacklist))

官方 0.9.4 SDK 的 `context.py`/`socketutil.py` 使用进程级默认 socket；这是同步结果迭代器不能交叉查询的工程依据。`ResultData.next()` 在接收结果为空时也可能返回 False，因此仅检查初次 `error_code == '0'` 或迭代结束不能独立证明完整；应复核末次状态、数量与日期覆盖。以上为发布包静态观察，非实际请求验证。([SDK 源码发布包](https://files.pythonhosted.org/packages/66/61/8ad575eb83d4b22852a922e669bc13c9723675343e885b0040ab11a6a321/baostock-0.9.4.tar.gz))

## 回测设计必须处理的偏差

- **停牌与缺失值：** 使用 `tradestatus` 区分停牌；对空字段做显式缺失值处理。停牌日的重复收盘价不是可成交价格。([每日更新 API](https://www.baostock.com/mainContent?file=DailyUpdates.md))
- **复权与成交：** 保存未复权行情和复权因子，复权序列用于信号计算；模拟成交和持仓现金流时明确使用原始价格并单独处理分红等公司行动。该建议基于官网披露的复权算法和除权除息/因子接口。([K 线接口](https://www.baostock.com/mainContent?file=stockKData.md)、[本地复权计算说明](https://www.baostock.com/mainContent?file=localdatafactorInfo.md)、[除权除息](https://www.baostock.com/mainContent?file=dividInfo.md))
- **财务数据前视：** 季报有 `pubDate`（披露日）与 `statDate`（报告期末），快报另有 `performanceExpUpdateDate`。日期不是具体公告时刻，也不证明源返回的是首次披露原值；设计上未知时刻默认下一交易日可用，修订数据不回填到更早的信息集。官网记载过历史季报字段修订，无法取得当时版本时注明为当前回溯数据。([季频快报](https://www.baostock.com/mainContent?file=seasonExpress.md)、[Python API 文档](https://www.baostock.com/mainContent?file=pythonAPI.md)、[数据调整记录](https://www.baostock.com/mainContent?file=modifyRecord.md))
- **宏观统计期与发布时间：** 月/年货币供应量现行字段只列统计年月/年份，没有具体发布日期；不能直接把统计期末当作可用日。存贷款等接口若有发布字段也须核验实际含义。无发布证据的系列默认用于浏览，不能自动获得历史因子资格。([月度供应量](https://www.baostock.com/mainContent?file=supplyData.md)、[年度供应量](https://www.baostock.com/mainContent?file=supplyDataYear.md)、[存款利率](https://www.baostock.com/mainContent?file=depositRate.md))
- **幸存者偏差：** 用 `query_all_stock(day)` 构造历史当日证券池；仅用当前证券清单或当前行业分类做历史回测会遗漏退市证券或错误套用现行分类。行业接口的日期参数及每周更新节奏需结合实际历史数据验证。([证券元信息](https://www.baostock.com/mainContent?file=StockBasicInfoAPI.md)、[行业分类](https://www.baostock.com/mainContent?file=stockIndustry.md))
- **数据源边界：** 官方页面没有在所核对的接口范围内承诺逐笔数据、实时行情、新闻、公告全文或公司画像等同花顺式功能；若产品需要这些内容，应另行核实数据源和授权。BaoStock页面称其免费、开源，但这不等同于已经确认数据的商业展示或再分发权。([平台介绍](https://www.baostock.com/mainContent?file=home.md)、[Python API 文档](https://www.baostock.com/mainContent?file=pythonAPI.md))

## 当前导出方法与历史说明的区分

规划接入的完整方法目录已经补入[开发方案](../development-plan.md)，按 0.9.4 发布包 `__init__.py` 导出的数据方法核对，包含六类季频、两类业绩、五类宏观以及三个每日批量方法。主页旧版本记录曾提到 `query_shibor_data()`，但当前导出及官网接口菜单未包含该方法，不能据旧记录承诺当前可用。历史版本记录的快报/预告起点还有不同表述，覆盖优先以现行专门接口页说明并由实际返回复核。([官方发布包](https://pypi.org/project/baostock/)、[平台介绍](https://www.baostock.com/mainContent?file=home.md)、[快报](https://www.baostock.com/mainContent?file=seasonExpress.md)、[预告](https://www.baostock.com/mainContent?file=seasonForecast.md))

除权除息接口包括登记日、除权日、派息日与红股上市日，税后现金字段可以是包含多种税率结果的文本；该结构支持按事件细分处理，也说明不能把所有字段当单值浮点或把除权日当派息日。([除权除息](https://www.baostock.com/mainContent?file=dividInfo.md))
