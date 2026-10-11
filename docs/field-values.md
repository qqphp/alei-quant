# 来源字段与显示口径

2026-10-10 重新读取 BaoStock 官网页面使用的 Markdown 正文：六类财务、快报、预告及五类宏观，共十三份专门接口说明。网页通过 POST `/helpdocs/api/markdown/<文件名>` 加载；直接 GET 返回 405。原始资料保存在忽略目录 `runtime/official-docs`，页面字段映射保存于 `frontend/src/data-fields.json`，每项保留官方说明链接。

## 数值处理

- 原始数据库与 CSV 保留 SDK 字符串。空字符串、缺失与不适用字段显示为“—”，不转换为零；真实零值保留。
- 财务表展示原始比例，不统一乘以 100。例如真实浦发 2026 半年报告的 `roeAvg=0.037501` 与 `npMargin=0.334346` 原样展示。官方财务样例也使用小数比例，即使描述含百分号，不能把它与所有其它接口的百分数值混为一种单位。
- 快报 ROE、业绩增长字段、预告、宏观利率的返回尺度可能不同。保留各接口原值；没有明确单位的列不自行添加百分号或货币倍数，不用于未经适配的公式。
- 官方明确的净利润、营业收入为元；每股收益依其算法为元/股；股本显示股；周转天数为天，周转次数为次。年度货币供应量明确为亿元，月度专门字段表未明确单位，当前保持原值。
- 日频行情价格为人民币元，成交额为元，股票成交量为股、ETF 为份。详情成交额除以一亿后标注“亿”，ETF 报价保留三位小数；日频换手和涨跌幅使用源百分数值。

## 时点与覆盖

财务公告日、报告期、快报更新日分别保留。回测仅接收清单声明且通过日期政策过滤的数据；页面浏览可见的后续报告不会因此自动成为更早策略的输入。

财务更新回看最近八个已结束季度，未披露季度可以成功返回空。用户需要更早年份时通过数据中心按证券、年份、季度采集。宏观接口的最后返回日期与数值缺失应直接阅读；方法调用成功不证明它覆盖到了当前月份。

来源：[盈利能力](https://www.baostock.com/mainContent?file=seasonProfit.md)、[营运能力](https://www.baostock.com/mainContent?file=seasonOperation.md)、[快报](https://www.baostock.com/mainContent?file=seasonExpress.md)、[预告](https://www.baostock.com/mainContent?file=seasonForecast.md)、[月度货币](https://www.baostock.com/mainContent?file=supplyData.md)、[年度货币](https://www.baostock.com/mainContent?file=supplyDataYear.md)。其它字段的专门来源随映射记录。
