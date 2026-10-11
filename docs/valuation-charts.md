# 个股估值曲线

市场行情的副图可以选择 PE TTM、PB MRQ、PS TTM、PCF NCF TTM。只在 A 股日线开放，与当前图表日期和固定数据版本一致；周/月、分钟、指数和 ETF 不填造估值。

四条序列直接取 BaoStock 日线的 `peTTM`、`pbMRQ`、`psTTM`、`pcfNcfTTM`。价格切换前复权或后复权时，估值比值保持原值，不乘价格展示比例。空值、非法值断开连线，不当作零，不前填；合法零值和负值保留。页脚统计当前加载窗口的有效/缺失点数，不代表全历史覆盖。

切换到不支持的周期或资产时副图恢复成交量，估值选项禁用。来源正确性、原始发布时间与历史修订真值不由曲线补证。

## 官方来源与验收

- 官网 [日线估值文档](https://www.baostock.com/mainContent?file=valuationDaily.md) 的实际 POST 正文已保存为 [valuationDaily.md](research/field-docs/valuationDaily.md)：HTTP 200、2197 字节、SHA256 `c196b69f5375b9fb8659f7f924547a4c1072b8d587fe5ef9c3f07401f9155718`。正文限定 A 股日线，指数与其它频率不包含这些字段。
- 固定版本 21328，八证券、三种价格展示共 24 个视图，5647 条唯一日线来源。四字段全部 67764 个点与原始行数值及日期逐项相等。实际编译执行前端辅助函数，另核对空串、非法值、零、负数和空序列；源/API 核对零 SDK 调用与主库写入。
- 浏览器实际选择四种估值，浦发银行 910 点；前复权价格下比值不变，周线恢复成交量并禁用四选项。TypeScript 与生产构建通过，既有大包提示保留。

证据：[verification-valuation.json](verification-valuation.json)。页面：[估值截图](screenshots/valuation-pe.png)。
