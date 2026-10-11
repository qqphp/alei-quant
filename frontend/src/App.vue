<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, nextTick, ref, watch } from 'vue'
import { Activity, ArrowDownToLine, ArrowUpRight, ChevronLeft, ChevronRight, Database, FolderPlus, Layers3, Play, RefreshCw, Search, Settings2, Star, X } from '@lucide/vue'
import type { EChartsOption } from 'echarts'
import Chart from './components/Chart.vue'
import { api, number, percent, dateTime } from './api'
import sourceFields from './data-fields.json'
import { drawdowns, tradeMarkers } from './report-charts'
import { valuationFields, valuationSeries } from './valuation'

type Tab = 'market' | 'research' | 'strategies' | 'data'
const tab = ref<Tab>('market')
const tabs = [{ id: 'market', label: '市场行情' }, { id: 'research', label: '回测研究' }, { id: 'strategies', label: '策略模型' }, { id: 'data', label: '数据中心' }] as const
const error = ref(''), notice = ref(''), busy = ref(false), health = ref<any>(null), overview = ref<any>({ datasets: [], indices: [], usage: [] })
const instruments = ref<any[]>([]), total = ref(0), page = ref(1), query = ref(''), instrumentType = ref('1'), withBars = ref(true), watchOnly = ref(false)
const sort = ref('code'), direction = ref('asc'), selected = ref<any>(null), detail = ref<any>(null), priceData = ref<any>({ data: [] })
const screening = ref({ pe_min: '', pe_max: '', pb_min: '', pb_max: '', amount_min: '', industry: '', exchange: '', status: '' })
const savedFilters = ref<any[]>([]), selectedFilter = ref(''), filterName = ref('')
const marketAsset = ref('1'), marketRanking = ref('gainers')
const marketSnapshot = computed(()=>overview.value.market_summary)
const marketGroup = computed(()=>marketSnapshot.value?.groups[marketAsset.value])
const marketMovers = computed(()=>marketGroup.value?.[marketRanking.value] || [])
const frequency = ref('d'), adjustment = ref('raw'), detailTab = ref('profile'), indicator = ref('volume')
const valuationField = computed(()=>valuationFields.find(item=>item.field===indicator.value))
const valuationData = computed(()=>valuationField.value?valuationSeries(priceData.value.data,valuationField.value.field):null)
watch([frequency,()=>selected.value?.type],()=>{ if(valuationField.value && (frequency.value!=='d' || selected.value?.type!=='1')) indicator.value='volume' })
const jobs = ref<any[]>([]), focusedJob = ref<any>(null), strategies = ref<any[]>([]), runs = ref<any[]>([]), catalogue = ref<any[]>([])
const runPage = ref(1), runTotal = ref(0)
const jobPage = ref(1), jobTotal = ref(0), activeJobs = ref(0), logPage = ref(1)
const installedPath = ref(''), activeStrategy = ref(''), report = ref<any>(null), compared = ref<string[]>([]), comparison = ref<any[]>([])
const modelParameters = ref<Record<string, any>>({})
const researchList = ref<any[]>([]), researchId = ref(''), research = ref<any>(null)
const researchName = ref('双均线参数研究'), candidateText = ref('')
const researchIntervals = ref({ train:{start:'2024-06-01',end:'2024-12-31'}, validation:{start:'2025-01-01',end:'2025-12-31'}, out_of_sample:{start:'2026-01-01',end:'2026-10-09'} })
const stageNames: Record<string,string> = {train:'训练',validation:'验证',out_of_sample:'样本外'}
const researchReady = computed(()=>research.value?.runs.length===research.value?.candidates.length*2 && research.value?.runs.every((r:any)=>r.status==='completed'))
const financialCategory = ref('profit')
const dataset = ref('profit'), datasetCode = ref('sh.600000'), datasetData = ref<any>({ data: [], total: 0 })
const datasetPage = ref(1), sync = ref<any>({ enabled: true, codes: [], periods: ['w','m'], minutes: [] }), syncCodes = ref('')
const universeDay = ref('2023-01-03'), universeQuery = ref(''), universePage = ref(1)
const universeData = ref<any>({data:[],total:0}), universeCoverage = ref<any>(null)
const universeExportUrl = computed(()=>'/api/universe/export?'+new URLSearchParams({day:universeData.value.day || universeDay.value,version:String(universeData.value.version ?? overview.value.version)}))
const datasetExportUrl = computed(()=>'/api/datasets/'+dataset.value+'/export?'+new URLSearchParams({code:datasetCode.value,version:String(datasetData.value.version ?? overview.value.version)}))
const reportSection = ref('trades'), reportPage = ref(1), reportPanel = ref<HTMLElement|null>(null)
const reportCurve = ref('equity'), tradeChartCode = ref('')
const curveNames: Record<string,string> = {equity:'日末净值',drawdown:'日末回撤',intraday_equity:'分钟收盘净值',intraday_drawdown:'分钟收盘回撤'}
const tradeChartContext = ref<any>(null)
const tradeChartPoints = computed(()=>tradeMarkers(priceData.value.data, tradeChartContext.value?.trades || [], frequency.value))
const reportRows = computed(()=>reportResult.value?.[reportSection.value] || [])
const frequencyNames: Record<string,string> = {d:'日线',w:'周线',m:'月线','5':'5分钟','15':'15分钟','30':'30分钟','60':'60分钟'}
const reportIsMinute = computed(()=>['5','15','30','60'].includes(reportResult.value?.signal_frequency))
const reportFields: Record<string,string[]> = { trades: ['date','code','side','quantity','price','fees'], orders: ['signal_date','execution_date','code','side','quantity','filled','status','reason'], cashflows: ['date','code','type','cash','receivable','bonus'], equity: ['date','nav','cash','frozen_cash','receivable','positions','sellable','value','benchmark'], intraday_equity: ['at','nav','cash','frozen_cash','receivable','positions','sellable','value'], signal_events: ['at','period','valuation_date'] }
const reportColumns = computed(()=> {
  if(reportIsMinute.value && reportSection.value==='trades') return ['signal_at','execution_at','code','side','quantity','price','fees']
  if(reportSection.value==='orders') {
    const columns = reportIsMinute.value?['signal_at','execution_at','code','side','quantity','filled','status','reason']:reportFields.orders
    return reportRows.value.some((row:any)=>row.corporate_action_adjustment)?[...columns,'corporate_action_adjustment']:columns
  }
  if(reportSection.value==='financial_reports') return ['dataset','code','period','published','updated','available_after','digest','payload']
  if(reportSection.value==='universe_snapshots') return ['day','source_batch','data_version','available_at','members','digest']
  if(reportSection.value==='signal_events') return [...reportFields.signal_events,...(reportResult.value?.financial_reports?['financial_refs']:[]),...(reportResult.value?.universe_snapshots?['universe_ref']:[])]
  return reportFields[reportSection.value] || []
})
const reportLabels: Record<string,string> = { day:'来源池日期',source_batch:'完整快照批次',data_version:'数据版本',available_at:'平台就绪时刻',members:'源成员数',universe_ref:'当次历史池批次', corporate_action_adjustment:'送转订单调整',dataset:'数据族',published:'公告日',updated:'更新日',available_after:'保守可用规则',digest:'财报内容摘要',payload:'来源财报原值',financial_refs:'策略可见财报',at:'上海时刻',signal_at:'信号时刻',execution_at:'执行时刻',period:'行情周期末日/时刻',valuation_date:'账户与财务截止日',date:'日期',code:'证券',side:'方向',quantity:'数量',price:'成交价',fees:'总费用',signal_date:'信号日',execution_date:'执行日',filled:'成交数量',status:'状态',reason:'原因',type:'事项',cash:'现金（元）',frozen_cash:'冻结现金（元）',sellable:'可卖数量（股/份）',receivable:'应收（元）',bonus:'送股',nav:'净资产（元）',positions:'持仓（股/份）',value:'策略净值',benchmark:'基准净值' }
function reportCell(row:any, field:string) {
  const value = row[field]
  if(field==='corporate_action_adjustment') return value?`${value.original.quantity} → ${value.quantity} 股；目标 ${percent(value.target_weight)}；开盘 ${number(value.opening_price)} 元；含权益净资产 ${number(value.opening_nav)} 元`:'—'
  if(field==='dataset') return catalogue.value.find((item:any)=>item.dataset===value)?.label || value
  if(field==='digest') return value?.slice(0,16)
  if(field==='available_after') return value+' 后首个交易日'
  if(field==='financial_refs') return value?.map((digest:string)=> {
    const source = reportResult.value.financial_reports.find((r:any)=>r.digest===digest)
    return source?source.code+' / '+source.period+'（公告 '+source.published+'）':digest.slice(0,16)
  }).join('；') || '—'
  if (field==='fees') return number(value?.total)
  if (field==='side') return value==='buy'?'买入':'卖出'
  if (field==='status') return ({filled:'全部成交',partial:'部分成交',rejected:'拒绝',expired:'到期',cancelled:'已撤销'} as Record<string,string>)[value] || value
  return value == null ? '—' : typeof value==='object' ? JSON.stringify(value) : value
}
const method = ref('query_profit_data'), argumentsText = ref('{\n  "code": "sh.600000",\n  "year": "2025",\n  "quarter": 1\n}')
const collect = ref({ codes: 'sh.600000,sz.000001,sh.600519,sh.000300', start: '2023-01-01', end: new Date().toLocaleDateString('sv-SE'), frequency: 'd' })
const form = ref({ codes: 'sh.600000,sz.000001,sh.600519', start: '2024-06-01', end: new Date().toLocaleDateString('sv-SE'), benchmark: 'sh.000300', capital: 1000000, fast: 10, slow: 30, exposure: 0.95, commission_rate: 0.0003, minimum_commission: 5, transfer_rate: 0.00001, slippage: 0.0005, participation: 0.05, dividend_tax: 0.1, risk_free: 0.02 })
let polling: ReturnType<typeof setInterval> | undefined
const stockTypes: Record<string, string> = { '1': '股票', '2': '指数', '3': '其他', '4': '可转债', '5': 'ETF' }
const statusNames: Record<string, string> = { waiting: '等待额度 / 续跑', queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
const labels: Record<string, string> = { date: '日期', code: '证券代码', pubDate: '公告日期', statDate: '报告期', updateDate: '更新日期', roeAvg: '净资产收益率', npMargin: '净利率', gpMargin: '毛利率', netProfit: '净利润', epsTTM: '每股收益 TTM', MBRevenue: '主营业务收入', peTTM: '市盈率 TTM', pbMRQ: '市净率', performanceExpPubDate: '快报公告日', performanceExpUpdateDate: '快报更新日', dividPlanAnnounceDate: '方案公告日', dividPlanDate: '实施公告日', dividOperateDate: '除权日', dividPayDate: '派息日', dividRegistDate: '登记日', dividCashPsBeforeTax: '每股税前股利', dividStocksPs: '每股送股', dividReserveToStockPs: '每股转增', dividStockMarketDate: '红股上市日' }
const changeClass = (value: unknown) => Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : ''
const chosenStrategy = computed(() => strategies.value.find(s => s.digest === activeStrategy.value))
const quotes = computed(() => selected.value?.quote || {})
const selectedFinancial = computed(() => detail.value?.financial?.[financialCategory.value] || [])
const reportResult = computed(() => report.value?.result)
const reportMetrics = computed(() => reportResult.value?.metrics)
const reportEtfs = computed(()=>Object.entries(report.value?.manifest.asset_rules || {}).filter(([,rule]:[string,any])=>rule.kind==='etf') as [string,any][])
const reportEtfActions = computed(()=>report.value?.manifest.supplemental_actions || [])
const reportGrowth = computed(()=>Object.entries(report.value?.manifest.asset_rules || {}).filter(([,rule]:[string,any])=>rule.board) as [string,any][])
const reportCorrections = computed(()=>(reportResult.value?.corporate_actions || []).filter((event:any)=>event.verified_revision))
const availableCount = computed(() => overview.value.datasets.find((d: any) => d.dataset === 'bars:d')?.revisions || 0)
const selectedEndpoint = computed(() => catalogue.value.find(e => e.method === method.value))
watch(activeStrategy, () => { modelParameters.value = Object.fromEntries(Object.entries(chosenStrategy.value?.manifest.parameters || {}).map(([key, value]: [string, any]) => [key, value.default])) })
const financeNames: Record<string, string> = { profit: '盈利能力', operation: '营运能力', growth: '成长能力', balance: '偿债能力', cash_flow: '现金流', dupont: '杜邦分析', express: '业绩快报', forecast: '业绩预告' }

async function action(work: () => Promise<void>, message = '') {
  error.value = ''; busy.value = true
  try { await work(); if (message) notice.value = message } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  finally { busy.value = false }
}
async function loadStocks() {
  const values = new URLSearchParams({ q: query.value, type: instrumentType.value, page: String(page.value), size: '30', sort: sort.value, direction: direction.value, with_bars: String(withBars.value), watch_only: String(watchOnly.value) })
  Object.entries(screening.value).forEach(([key, value]) => { if (value !== '') values.set(key, String(value)) })
  const result = await api('/instruments?' + values)
  instruments.value = result.data; total.value = result.total
  if (!selected.value && result.data.length) await chooseStock(result.data[0])
  else if (selected.value) {
    const updated = result.data.find((item: any) => item.code === selected.value.code)
    if (updated) await chooseStock(updated)
  }
}
async function saveFilter() {
  const config = { q: query.value, type: instrumentType.value, sort: sort.value, direction: direction.value, with_bars: withBars.value, watch_only: watchOnly.value,
    ...Object.fromEntries(Object.entries(screening.value).filter(([,v])=>v!=='').map(([k,v])=>[k,['industry','exchange','status'].includes(k)?v:Number(v)])) }
  const value = await api('/filters', 'POST', { name: filterName.value, config })
  savedFilters.value = await api('/filters'); selectedFilter.value = value.id; notice.value = '筛选条件已保存。'
}
async function applyFilter() {
  const config = savedFilters.value.find(item=>item.id===selectedFilter.value)?.config
  if (!config) return
  query.value=config.q; instrumentType.value=config.type; sort.value=config.sort; direction.value=config.direction
  withBars.value=config.with_bars; watchOnly.value=config.watch_only
  Object.keys(screening.value).forEach(key=>{ (screening.value as any)[key]=config[key] ?? '' })
  page.value=1; await loadStocks()
}
async function chooseStock(item: any) {
  if(tradeChartContext.value && tradeChartContext.value.code!==item.code) tradeChartContext.value=null
  if (selected.value?.code !== item.code) { detail.value = null; priceData.value = { data: [] } }
  selected.value = item
  await Promise.all([loadChart(), api('/instruments/' + item.code).then(value => { if (selected.value?.code === item.code) detail.value = value })])
}
async function loadChart() {
  if (!selected.value) return
  const code=selected.value.code
  const context=tradeChartContext.value
  const params=new URLSearchParams({ start:context?.start || collect.value.start, end:context?.end || collect.value.end, frequency:frequency.value, adjustment:adjustment.value })
  if(context) params.set('version',String(context.version))
  const values=await api('/bars/'+code+'?'+params)
  if(selected.value?.code===code) priceData.value=values
}
async function loadRuns() {
  const requestedPage=runPage.value
  const result = await api(`/backtests?paged=true&page=${requestedPage}&size=20`)
  if(runPage.value===requestedPage) { runs.value=result.data; runTotal.value=result.total }
}
async function loadJobs() {
  const requestedPage=jobPage.value
  const result=await api(`/jobs?paged=true&page=${requestedPage}&size=20`)
  if(jobPage.value===requestedPage) { jobs.value=result.data; jobTotal.value=result.total; activeJobs.value=result.active_count }
}
async function refresh() {
  const values = await Promise.all([api('/health'), api('/overview'), loadJobs(), api('/strategies'), loadRuns(), api('/catalog'), api('/filters')])
  ;[health.value, overview.value, , strategies.value, , catalogue.value, savedFilters.value] = values
  if (!strategies.value.some(s=>s.digest===activeStrategy.value && s.enabled)) activeStrategy.value = (strategies.value.find(s=>s.enabled && s.id==='ma_cross') || strategies.value.find(s=>s.enabled))?.digest || ''
}
async function toggleWatch(item: any) {
  await api('/watchlist/' + item.code, item.watched ? 'DELETE' : 'POST')
  item.watched = !item.watched
}
function orderBy(field: string) {
  direction.value = sort.value === field && direction.value === 'asc' ? 'desc' : 'asc'
  sort.value = field; page.value = 1; void action(loadStocks)
}
async function submitCollection() {
  const result = await api('/collect', 'POST', { ...collect.value, codes: collect.value.codes.split(/[，,\s]+/).filter(Boolean) })
  notice.value = '采集任务已入队，页面查询继续使用已发布数据。'; tab.value = 'data'
  await refresh(); await focusJob(result.job_id)
}
async function submitOperation() {
  const result = await api('/collect', 'POST', { operations: [{ method: method.value, arguments: JSON.parse(argumentsText.value) }] })
  await refresh(); await focusJob(result.job_id); notice.value = '接口采集任务已入队。'
}
async function focusJob(id: string) {
  if(focusedJob.value?.id!==id) { logPage.value=1; focusedJob.value={id,logs:[],logs_total:0} }
  const requestedPage=logPage.value
  const result=await api(`/jobs/${id}?log_page=${requestedPage}&log_size=100`)
  if(focusedJob.value?.id===id && logPage.value===requestedPage) focusedJob.value=result
}
function runConfig() {
  if (!chosenStrategy.value) throw new Error('请先安装并启用一个策略')
  const { fast, slow, exposure, codes, ...config } = form.value
  return { ...config, codes: codes.split(/[，,\s]+/).filter(Boolean), parameters: modelParameters.value, strategy_id: chosenStrategy.value.id, strategy_version: chosenStrategy.value.version, strategy_digest: chosenStrategy.value.digest }
}
async function submitRun() {
  const result = await api('/backtests', 'POST', runConfig())
  notice.value = '回测已提交，模型、参数和数据版本已固定。'
  runPage.value=1; await refresh(); await showReport(result.job_id)
}
async function showReport(id: string, scroll = true) {
  if(report.value?.id!==id) { reportPage.value=1; reportCurve.value='equity'; reportSection.value='trades'; tradeChartCode.value='' }
  report.value = await api('/backtests/' + id); tab.value = 'research'
  if(scroll) { await nextTick(); reportPanel.value?.scrollIntoView({behavior:'smooth',block:'start'}) }
}
async function showMarketMover(item: any) {
  const result = await api('/instruments?'+new URLSearchParams({q:item.code,size:'30'}))
  const stock = result.data.find((row:any)=>row.code===item.code)
  if (!stock) throw new Error('该证券档案暂不可读。')
  tradeChartContext.value=null
  await chooseStock(stock)
}
async function showTradeChart() {
  if(!reportResult.value) return
  const code=tradeChartCode.value || report.value.manifest.config.codes[0]
  const config=report.value.manifest.config
  const found=await api('/instruments?'+new URLSearchParams({q:code,size:'30'}))
  const item=found.data.find((value:any)=>value.code===code)
  if(!item) throw new Error('该报告证券尚无本地基本资料')
  tradeChartContext.value={code,run:report.value.id,version:report.value.manifest.data_version,start:config.start,end:config.end,trades:reportResult.value.trades.filter((trade:any)=>trade.code===code)}
  frequency.value=reportIsMinute.value?reportResult.value.signal_frequency:'d'; adjustment.value='raw'
  await chooseStock(item); tab.value='market'
}
async function compareRuns() {
  comparison.value = await Promise.all(compared.value.map(id => api('/backtests/' + id)))
  const dimensions = ['data_version','engine_version','engine_source','python_version','dependencies','rules_version','seed']
  const costFields = ['start','end','benchmark','codes','capital','commission_rate','minimum_commission','transfer_rate','slippage','participation','dividend_tax','risk_free']
  const differs = dimensions.some(key=>new Set(comparison.value.map(r=>JSON.stringify(r.manifest[key]))).size>1) || costFields.some(key=>new Set(comparison.value.map(r=>JSON.stringify(r.manifest.config[key]))).size>1)
  notice.value = differs ? '这些运行的日期、股票池、资金、数据/引擎/规则/环境版本、成本或基准不同；此图仅供并列查看，不能据此判断参数优劣。' : '比较使用相同区间、股票池、资金、数据/引擎/规则/环境版本、成本与基准；参数选择仍需独立验证和样本外区间。'
}
async function loadResearch() { research.value = researchId.value ? await api('/experiments/'+researchId.value) : null }
async function createResearch() {
  const candidates = JSON.parse(candidateText.value)
  if (!Array.isArray(candidates)) throw new Error('候选参数须为 JSON 数组，每项是一组模型参数')
  const value = await api('/experiments','POST',{name:researchName.value,config:runConfig(),intervals:researchIntervals.value,candidates})
  researchList.value=await api('/experiments'); researchId.value=value.experiment_id; await loadResearch(); await refresh()
  notice.value='训练与验证任务已提交；数据、模型、引擎、成本及区间已固定，尚未运行样本外区间。'
}
async function lockResearch(candidate:number) {
  await api('/experiments/'+researchId.value+'/lock','POST',{candidate}); await loadResearch(); await refresh()
  notice.value='参数已锁定并提交样本外任务。此研究不再改换参数，失败任务可按原清单重跑。'
}
async function installStrategy() { await api('/strategies/install', 'POST', { path: installedPath.value }); await refresh() }
async function toggleStrategy(strategy: any) {
  await api(`/strategies/${strategy.id}/${strategy.version}/${strategy.digest}?enabled=${!strategy.enabled}`, 'PATCH'); await refresh()
}
function resetDataset() { datasetPage.value=1; datasetData.value={data:[],total:0} }
async function loadDataset() {
  const currentDataset=dataset.value, currentCode=datasetCode.value, currentPage=datasetPage.value
  const values = new URLSearchParams({ code: currentCode, size:'100', page:String(currentPage) })
  if(currentPage>1 && datasetData.value.version!==undefined) values.set('version',String(datasetData.value.version))
  const result = await api('/datasets/' + currentDataset + '?' + values)
  if(dataset.value===currentDataset && datasetCode.value===currentCode && datasetPage.value===currentPage) datasetData.value=result
}
async function loadUniverse() {
  const values = new URLSearchParams({day:universeDay.value,q:universeQuery.value,page:String(universePage.value),size:'100'})
  if(universePage.value>1 && universeData.value.version!==undefined) values.set('version',String(universeData.value.version))
  universeData.value={data:[],total:0,...await api('/universe?'+values)}
}
async function checkUniverseCoverage() {
  universeCoverage.value=await api('/universe/coverage?'+new URLSearchParams({start:collect.value.start,end:collect.value.end}))
}
async function fillUniverse() {
  const value=await api('/universe/backfill','POST',{start:collect.value.start,end:collect.value.end})
  notice.value=value.job_id?'已入队 '+value.days+' 个交易日证券池回填；'+value.note:value.note
  await refresh(); if(value.job_id) await focusJob(value.job_id)
}
async function saveSync() {
  await api('/sync','POST',{ ...sync.value, codes: syncCodes.value.split(/[，,\s]+/).filter(Boolean) }); notice.value='本机定时同步设置已保存，工作进程每分钟检查。'
}
function fieldLabel(field: string, etfAnnouncement = false) {
  if(etfAnnouncement) {
    const announcementLabels: Record<string,string> = { sha256:'原件 SHA256',source:'公告来源',document:'本地原件',tax_rate:'分红税率',source_type:'来源类型',announcement_date:'公告日期',dividCashPsBeforeTax:'每份现金（元）',dividStocksPs:'每份送份',dividReserveToStockPs:'每份转增' }
    if(announcementLabels[field]) return announcementLabels[field]
  }
  const item=(sourceFields as Record<string,{label:string,unit:string}>)[field]
  return item ? item.label+(item.unit?'（'+item.unit+'）':'') : labels[field] || field
}
function fieldDescription(field: string) {
  return (sourceFields as Record<string,{description:string}>)[field]?.description || '来源未提供该字段说明，保留原值。'
}
function fieldUnit(field: string) {
  if(field==='code') return '完整证券代码'
  if(field==='statYear') return '年'
  if(field==='statMonth') return '月'
  if(field.endsWith('Date') || field==='date' || field==='calendar_date') return 'YYYY-MM-DD'
  if(['code_name','industry','industryClassification','profitForcastAbstract','dividCashStock'].includes(field)) return '来源文本'
  if(['status','type','tradeStatus','tradestatus','isST','is_trading_day','adjustflag','profitForcastType'].includes(field)) return '编码原值（见含义）'
  return (sourceFields as Record<string,{unit:string}>)[field]?.unit || '文档未注明单位；原值未换算'
}
function sourceCell(value:unknown) { return value===null || value===undefined || value==='' ? '—' : value }
function rowFields(data: any[]) { return data.length ? Object.keys(data[0]) : [] }

const candleOptions = computed<EChartsOption>(() => {
  const bars = priceData.value.data
  const dates = bars.map((b: any) => b.time ? b.date+' '+b.time.slice(8,10)+':'+b.time.slice(10,12) : b.date)
  return {
    animation: false, color: ['#c3963e', '#537baa', '#735b9a'], backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['K线', 'MA10', 'MA30', ...(valuationField.value?[valuationField.value.label]:indicator.value==='macd'?['DIF','DEA']:[])], top: 4, right: 16, textStyle: { color: '#74818b' } },
    grid: [{ top: 38, left: 56, right: 22, height: '54%' }, { top: '71%', left: 56, right: 22, height: '13%' }],
    xAxis: [{ type: 'category', data: dates, boundaryGap: true, axisLine: { lineStyle: { color: '#dae2e6' } }, axisLabel: { color: '#87929b', hideOverlap: true } }, { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false } }],
    yAxis: [{ scale: true, splitLine: { lineStyle: { color: '#edf1f3' } }, axisLabel: { color: '#87929b' } }, { gridIndex: 1, scale: true, splitNumber: 2, axisLabel: { formatter: (v: number) => valuationField.value || indicator.value==='macd'?v.toFixed(2):(v/10000).toFixed(0) + '万', color: '#87929b' }, splitLine: { show: false } }],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: tradeChartContext.value?0:65, end: 100 }, { type: 'slider', xAxisIndex: [0, 1], bottom: 4, height: 17, borderColor: 'transparent', fillerColor: '#0c887f20', handleStyle: { color: '#0c887f' } }],
    series: [
      { name: 'K线', type: 'candlestick', data: bars.map((b: any) => [b.open, b.close, b.low, b.high]), itemStyle: { color: '#d45d5d', color0: '#309d87', borderColor: '#d45d5d', borderColor0: '#309d87' }, markPoint: { data:tradeChartPoints.value.points, tooltip:{formatter:(params:any)=>`${params.data.name} · ${params.data.execution}<br/>原始成交价 ${number(params.data.value,4)} 元 · 数量 ${params.data.quantity}`} } },
      ...[10, 30].map((n, i) => ({ name: 'MA' + n, type: 'line' as const, data: bars.map((b: any) => b['ma' + n]), showSymbol: false, lineStyle: { width: 1.3, color: ['#c3963e', '#537baa'][i] } })),
      ...(valuationField.value?[{name:valuationField.value.label,type:'line' as const,xAxisIndex:1,yAxisIndex:1,showSymbol:false,connectNulls:false,data:valuationData.value!.values,lineStyle:{color:'#735b9a',width:1.5}}]:[{ name: indicator.value==='macd'?'MACD':'成交量', type: 'bar' as const, xAxisIndex: 1, yAxisIndex: 1, data: bars.map((b: any) => ({ value: indicator.value==='macd'?b.macd:b.volume, itemStyle: { color: (indicator.value==='macd'?b.macd>=0:b.close>=b.open) ? '#d45d5d99' : '#309d8799' } })) }]),
      ...(indicator.value==='macd'?['dif','dea'].map((field)=>({name:field.toUpperCase(),type:'line' as const,xAxisIndex:1,yAxisIndex:1,showSymbol:false,data:bars.map((b:any)=>b[field])})):[]),
    ],
  }
})
const equityOptions = computed<EChartsOption>(() => {
  const intraday = reportIsMinute.value && ['intraday_equity','intraday_drawdown'].includes(reportCurve.value)
  const values = reportResult.value?.[intraday?'intraday_equity':'equity'] || []
  if(['drawdown','intraday_drawdown'].includes(reportCurve.value)) return {animation:false,tooltip:{trigger:'axis',valueFormatter:(value:unknown)=>percent(value)},legend:{top:10,right:20,data:[intraday?'分钟收盘回撤':'日末回撤']},grid:{left:55,right:25,top:48,bottom:36},xAxis:{type:'category',data:values.map((point:any)=>intraday?point.at:point.date),axisLabel:{hideOverlap:true,color:'#84919a'}},yAxis:{max:0,axisLabel:{formatter:(value:number)=>percent(value)},splitLine:{lineStyle:{color:'#edf1f3'}}},series:[{name:intraday?'分钟收盘回撤':'日末回撤',type:'line',data:drawdowns(values),showSymbol:false,lineStyle:{color:'#d45d5d',width:2},areaStyle:{color:'#d45d5d18'}}]}
  return { animation: false, tooltip: { trigger: 'axis' }, legend: { top: 10, right: 20, data: intraday?['策略净值']:['策略净值', '基准（价格指数）'] }, grid: { left: 55, right: 25, top: 48, bottom: 36 }, xAxis: { type: 'category', data: values.map((e: any) => intraday?e.at:e.date), axisLabel: { hideOverlap: true, color: '#84919a' } }, yAxis: { scale: true, splitLine: { lineStyle: { color: '#edf1f3' } } }, series: [{ name: '策略净值', type: 'line', data: values.map((e: any) => e.value), showSymbol: false, lineStyle: { color: '#0d8a80', width: 2 }, areaStyle: { color: '#0d8a8010' } }, ...(intraday?[]:[{ name: '基准（价格指数）', type: 'line' as const, data: values.map((e: any) => e.benchmark), showSymbol: false, lineStyle: { color: '#97a7b5', type: 'dashed' as const } }])] }
})
const comparisonOptions = computed<EChartsOption>(() => {
  const dates: string[] = [...new Set<string>(comparison.value.flatMap(r => r.result?.equity.map((e: any) => e.date) || []))].sort()
  return { tooltip: { trigger: 'axis' }, legend: { top: 10, type: 'scroll' }, grid: { left: 55, right: 25, top: 48, bottom: 36 }, xAxis: { type: 'category', data: dates }, yAxis: { scale: true }, series: comparison.value.filter(r => r.result).map(r => {
    const byDate = new Map(r.result.equity.map((e: any) => [e.date,e.value])); return { name: r.id.slice(0, 8), type: 'line', showSymbol: false, connectNulls: false, data: dates.map(day=>byDate.get(day) ?? null) as (number|null)[] }
  }) }
})

onMounted(() => {
  void action(async () => { await refresh(); researchList.value=await api('/experiments'); sync.value=await api('/sync'); syncCodes.value=sync.value.codes.join(','); await loadStocks() })
  polling = setInterval(async () => {
    try { await refresh(); if (research.value) await loadResearch(); if (focusedJob.value) await focusJob(focusedJob.value.id); if (report.value && ['queued', 'running'].includes(report.value.status)) await showReport(report.value.id,false) }
    catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  }, 6000)
})
onBeforeUnmount(() => { if (polling) clearInterval(polling) })
</script>

<template>
  <div class="workspace">
    <header class="topbar">
      <a href="#" class="brand" @click.prevent="tab = 'market'"><span class="brand-icon"><Activity :size="21" /></span><span>阿雷量化<small>RESEARCH TERMINAL</small></span></a>
      <nav aria-label="主导航"><button v-for="item in tabs" :key="item.id" :class="{ active: tab === item.id }" @click="tab = item.id">{{ item.label }}</button></nav>
      <div class="connection"><i :class="{ connected: health }"></i>{{ health ? (health.worker?'数据库 / 后台已连接':'数据库已连接 · 后台未运行') : '连接中' }}<span>v0.1</span></div>
    </header>
    <div v-if="error" class="message error" role="alert">{{ error }}<button aria-label="关闭错误" @click="error = ''"><X :size="16" /></button></div>
    <div v-if="notice" class="message notice" role="status">{{ notice }}<button aria-label="关闭提示" @click="notice = ''"><X :size="16" /></button></div>
    <main>
      <div class="page-heading"><div><div class="eyebrow">LOCAL / BAOSTOCK / POSTGRESQL</div><h1>{{ tabs.find(t => t.id === tab)?.label }}</h1></div><div class="heading-actions"><span class="subtle">数据版本 {{ overview.version || 0 }} · 历史及盘后数据</span><button class="button" :disabled="busy" @click="action(async () => { await refresh(); await loadStocks() })"><RefreshCw :size="14" :class="{ spinning: busy }" />刷新</button></div></div>

      <template v-if="tab === 'market'">
        <section class="index-strip">
          <article v-for="item in [{code:'sh.000001',name:'上证指数'},{code:'sh.000300',name:'沪深 300'},{code:'sh.000905',name:'中证 500'},{code:'sz.399001',name:'深证成指'}]" :key="item.code"><span>{{ item.name }}<small>{{ item.code }}</small></span><template v-for="quote in [overview.indices.find((q: any) => q.code === item.code)]"><strong :class="changeClass(quote?.pctChg)">{{ number(quote?.close) }}</strong><div class="index-bottom"><span :class="changeClass(quote?.pctChg)">{{ quote?.pctChg ? (Number(quote.pctChg)>0?'+':'') + number(quote.pctChg) + '%' : '暂未采集' }}</span><small>{{ quote?.date || '可在数据中心补充' }}</small></div></template></article>
        </section>
        <section class="panel market-summary" aria-label="同日市场统计">
          <div class="panel-title"><h2>共同完整日 · {{ marketSnapshot?.date || '待核验' }}</h2><label>统计资产<select v-model="marketAsset" aria-label="统计资产"><option value="1">A 股</option><option value="5">ETF</option></select></label></div>
          <template v-if="marketSnapshot?.status==='complete' && marketGroup">
            <div class="breadth-stats"><span><small>成员</small><strong>{{ number(marketGroup.members,0) }}</strong></span><span><small>上涨</small><strong class="up">{{ number(marketGroup.up,0) }}</strong></span><span><small>下跌</small><strong class="down">{{ number(marketGroup.down,0) }}</strong></span><span><small>平盘</small><strong>{{ number(marketGroup.flat,0) }}</strong></span><span><small>停牌</small><strong>{{ number(marketGroup.paused,0) }}</strong></span><span><small>已知成交额</small><strong>{{ number(Number(marketGroup.amount)/100000000) }}<em>亿元</em></strong></span></div>
            <p class="market-stats-note">{{ marketGroup.trading }} 个交易 · 涨跌缺值 {{ marketGroup.unknown_change }} · 状态未知 {{ marketGroup.unknown_status }} · 交易成交额缺值 {{ marketGroup.missing_amount }}；不把缺值当成零。类型按当前档案，涨跌按来源 pctChg；停牌不参与排行。</p>
            <details class="market-movers"><summary>当日异动 · 查看前 10 名</summary><div class="inline-form"><label>榜单<select v-model="marketRanking" aria-label="市场榜单"><option value="gainers">涨幅榜</option><option value="losers">跌幅榜</option><option value="turnover">成交额榜</option></select></label><span class="subtle">{{ marketSnapshot.date }} · {{ marketGroup.asset }} · 同值按代码升序</span></div><div class="table-scroll"><table><thead><tr><th>证券</th><th>收盘价（元）</th><th>涨跌幅</th><th>成交额（亿元）</th></tr></thead><tbody><tr v-for="item in marketMovers" :key="item.code"><td><button class="text-button" @click="action(()=>showMarketMover(item))">{{ item.code_name }} · {{ item.code }}</button></td><td>{{ number(item.close,item.type==='5'?3:2) }}</td><td :class="changeClass(item.pctChg)">{{ item.pctChg==='' || item.pctChg===null || item.pctChg===undefined?'—':number(item.pctChg)+'%' }}</td><td>{{ item.amount==='' || item.amount===null || item.amount===undefined?'—':number(Number(item.amount)/100000000) }}</td></tr></tbody></table><div v-if="!marketMovers.length" class="empty">本资产当日没有符合该榜单条件的证券。</div></div></details>
            <p class="market-stats-note">{{ marketSnapshot.note }} · 证券池版本 {{ marketSnapshot.pool_version }} · 数据版本 {{ marketSnapshot.version }}</p>
          </template><p v-else class="market-stats-note">{{ marketSnapshot?.reason || '正在核验共同完整交易日。' }}</p>
        </section>
        <div class="coverage-line"><Database :size="13" /><span>{{ number(availableCount, 0) }} 条已发布日线修订<span v-for="item in overview.market_coverage" :key="item.asset"> · {{ item.date }} {{ item.asset }} {{ item.present }}/{{ item.expected }}（缺 {{ item.missing }}）</span> · 按当日证券池与当前类型映射核对；不代表全历史完整</span></div>
        <div class="market-layout">
          <section class="panel securities"><div class="panel-title"><h2>证券列表</h2><span class="tag">{{ total }} 个</span></div>
            <form class="search" @submit.prevent="page = 1; action(loadStocks)"><Search :size="16" /><input v-model="query" placeholder="搜索名称或证券代码" aria-label="搜索名称或证券代码" /><button type="submit">搜索</button></form>
            <div class="list-filters"><select v-model="instrumentType" aria-label="证券类型" @change="page=1; action(loadStocks)"><option value="">全部类型</option><option value="1">A 股</option><option value="2">指数</option><option value="5">ETF</option></select><label><input v-model="withBars" type="checkbox" @change="page=1; action(loadStocks)" />已有行情</label><label><input v-model="watchOnly" type="checkbox" @change="page=1; action(loadStocks)" />自选</label></div>
            <details class="screening"><summary>估值 / 行业筛选与排行</summary>
              <form class="form" @submit.prevent="page=1; action(loadStocks)">
                <div class="form-pair"><label>交易所<select v-model="screening.exchange" aria-label="筛选交易所"><option value="">全部交易所</option><option value="sh">上海</option><option value="sz">深圳</option></select></label><label>当前上市状态<select v-model="screening.status" aria-label="筛选上市状态"><option value="">全部状态</option><option value="1">上市</option><option value="0">退市</option></select></label></div>
                <div class="form-pair"><label>PE 下限<input type="number" v-model="screening.pe_min" step="any" /></label><label>PE 上限<input type="number" v-model="screening.pe_max" step="any" /></label><label>PB 下限<input type="number" v-model="screening.pb_min" step="any" /></label><label>PB 上限<input type="number" v-model="screening.pb_max" step="any" /></label></div>
                <label>最低成交额（元）<input type="number" v-model="screening.amount_min" min="0" /></label><label>行业名称<input v-model="screening.industry" placeholder="需与入库行业名称一致" /></label>
                <div class="form-pair"><label>排序<select v-model="sort"><option value="code">证券代码</option><option value="code_name">证券名称</option><option value="pctChg">涨跌幅</option><option value="amount">成交额</option><option value="peTTM">PE TTM</option><option value="pbMRQ">市净率</option></select></label><label>顺序<select v-model="direction"><option value="asc">由低到高</option><option value="desc">由高到低</option></select></label></div>
                <button class="button">应用筛选</button><label>筛选名称<input v-model="filterName" maxlength="80" /></label><button type="button" class="button" :disabled="!filterName.trim()" @click="action(saveFilter)">保存当前条件</button>
                <select v-model="selectedFilter" aria-label="保存的筛选" @change="action(applyFilter)"><option value="">选择已保存条件</option><option v-for="item in savedFilters" :value="item.id">{{ item.name }}</option></select><a v-if="selectedFilter" class="button" :href="'/api/filters/'+selectedFilter+'/export'"><ArrowDownToLine :size="14" />导出已保存条件</a><button v-if="selectedFilter" type="button" class="text-button" @click="action(async()=>{await api('/filters/'+selectedFilter,'DELETE');selectedFilter='';await refresh()})">删除所选条件</button>
                <p class="form-note">筛选使用各证券最新入库日期；缺少估值字段的证券不会通过相应区间筛选。导出仅包含所选已保存条件；修改条件后请先保存。上市状态来自当前档案，不是历史可交易状态。</p>
              </form>
            </details>
            <div class="table-scroll"><table class="stock-table"><thead><tr><th @click="orderBy('code')">名称 / 代码</th><th @click="orderBy('close')">最新价</th><th @click="orderBy('pctChg')">涨跌幅</th><th></th></tr></thead><tbody><tr v-for="item in instruments" :key="item.code" :class="{ selected: selected?.code === item.code }" @click="action(() => chooseStock(item))"><td><strong>{{ item.code_name }}</strong><small>{{ item.code }}</small></td><td :class="changeClass(item.quote?.pctChg)">{{ number(item.quote?.close,item.type==='5'?3:2) }}</td><td :class="changeClass(item.quote?.pctChg)">{{ item.quote?.pctChg ? number(item.quote.pctChg) + '%' : '—' }}</td><td><button class="icon-button" :aria-label="item.watched?'移出自选':'加入自选'" @click.stop="action(() => toggleWatch(item))"><Star :size="14" :class="{ starred: item.watched }" /></button></td></tr></tbody></table><div v-if="!instruments.length" class="empty">暂无匹配证券。可关闭“已有行情”或前往数据中心采集。</div></div>
            <div class="pagination"><span>{{ page }} / {{ Math.max(1, Math.ceil(total/30)) }}</span><button :disabled="page<=1" aria-label="上一页" @click="page--; action(loadStocks)"><ChevronLeft :size="16" /></button><button :disabled="page*30>=total" aria-label="下一页" @click="page++; action(loadStocks)"><ChevronRight :size="16" /></button></div>
          </section>
          <div class="security-main" v-if="selected">
            <section class="panel quote-panel"><div class="security-heading"><div><h2>{{ selected.code_name }}<span class="tag">{{ stockTypes[selected.type] }}</span></h2><p>{{ selected.code }}<span>{{ detail?.industry?.at(-1)?.industry || '行业尚未采集' }}</span></p></div><div class="price"><strong :class="changeClass(quotes.pctChg)">{{ number(quotes.close,selected.type==='5'?3:2) }}</strong><span :class="changeClass(quotes.pctChg)">{{ quotes.pctChg ? number(quotes.pctChg)+'%' : '暂无行情' }}</span><small>{{ quotes.date || '待采集' }} · 非实时</small></div></div>
              <dl class="quote-fields"><div v-for="item in [{label:'开盘',value:quotes.open,digits:selected.type==='5'?3:2},{label:'最高',value:quotes.high,digits:selected.type==='5'?3:2},{label:'最低',value:quotes.low,digits:selected.type==='5'?3:2},{label:'成交额',value:quotes.amount?Number(quotes.amount)/100000000:null,unit:' 亿'},{label:'PE TTM',value:quotes.peTTM},{label:'市净率',value:quotes.pbMRQ},{label:'昨收参考',value:quotes.preclose,digits:selected.type==='5'?3:2},{label:'成交量（股/份）',value:quotes.volume,digits:0},{label:'换手率',value:quotes.turn,unit:'%'},{label:'PS TTM',value:quotes.psTTM},{label:'PCF TTM',value:quotes.pcfNcfTTM},{label:'交易状态',value:quotes.tradestatus,digits:0}]" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.label==='交易状态' ? (item.value==='1'?'交易':item.value==='0'?'停牌':'—') : number(item.value,'digits' in item?item.digits:2) }}{{ item.value!==null && item.value!==undefined && item.value!=='' && 'unit' in item ? item.unit : '' }}</dd></div></dl>
              <div class="chart-toolbar"><div class="segmented"><button v-for="f in [{value:'d',label:'日 K'},{value:'w',label:'周 K'},{value:'m',label:'月 K'},{value:'5',label:'5 分钟'},{value:'15',label:'15 分钟'},{value:'30',label:'30 分钟'},{value:'60',label:'60 分钟'}]" :key="f.value" :class="{active:frequency===f.value}" @click="frequency=f.value; adjustment='raw'; tradeChartContext=null; action(loadChart)">{{ f.label }}</button></div><select v-model="indicator" aria-label="副图指标"><option value="volume">成交量</option><option value="macd">MACD 12/26/9</option><option v-for="item in valuationFields" :key="item.field" :value="item.field" :disabled="frequency!=='d' || selected?.type!=='1'">{{ item.label }}</option></select><select v-model="adjustment" aria-label="复权方式" @change="action(loadChart)"><option value="raw">不复权</option><option value="forward">前复权（展示）</option><option value="backward">后复权（展示）</option></select></div>
              <div v-if="tradeChartContext" class="inline-form dataset-form"><span class="subtle">运行 {{ tradeChartContext.run.slice(0,8) }} · 固定数据版本 {{ priceData.version }} · {{ tradeChartContext.start }} → {{ tradeChartContext.end }} · 买入/卖出 {{ tradeChartPoints.points.length }} 个成交点<span v-if="tradeChartPoints.missing.length">；{{ tradeChartPoints.missing.length }} 笔未在当前行情窗口定位</span></span><button class="button" @click="action(async()=>{tradeChartContext=null;await loadChart()})">返回最新行情</button></div>
              <p v-if="tradeChartContext" class="form-note">头部为最新盘后报价；K 线使用报告版本。标记为模拟成交，复权时仅换算图上坐标，提示保留原始成交价。切换周期退出买卖点视图；周/月信号以日线定位成交日。</p>
              <Chart v-if="priceData.data.length" :option="candleOptions" /><div v-else class="empty chart-empty"><Layers3 :size="30" /><p>该证券 / 周期尚无入库行情</p><button class="button" @click="collect.codes=selected.code; collect.frequency=frequency; tab='data'">采集这个周期</button></div>
              <div class="chart-foot">MA10 / MA30 · {{ valuationField?valuationField.label+' · 来源比值':indicator==='macd'?'MACD 12/26/9':'成交量：股 / 份' }} · 价格：人民币元<span>{{ priceData.data.length }} 根 K 线{{ priceData.not_ready?' · '+priceData.not_ready+' 根未到就绪时间':'' }}{{ priceData.truncated?'（区间已截断，请缩小日期范围）':'' }}</span></div>
              <p v-if="valuationField && priceData.version!==undefined" class="form-note" role="note">估值仅用 A 股日频来源原值，不随价格复权换算；显示窗口内 {{ valuationData?.available }} 个有效值、{{ valuationData?.missing }} 个缺值，缺值断开且不前填，负值和零保持。固定数据版本 {{ priceData.version }}。<a href="https://www.baostock.com/mainContent?file=valuationDaily.md" target="_blank" rel="noreferrer">官方日频估值说明</a></p>
              <p v-if="priceData.price_note" class="form-note">{{ priceData.price_note }}<span v-if="priceData.factor_version"> 因子版本 {{ priceData.factor_version }}。</span></p>
            </section>
            <section class="panel detail-panel"><div class="detail-tabs"><button v-for="item in [{id:'profile',label:'基本资料'},{id:'financial',label:'财务与业绩'},{id:'dividends',label:'分红送转'}]" :key="item.id" :class="{active:detailTab===item.id}" @click="detailTab=item.id">{{ item.label }}</button></div>
              <dl v-if="detailTab==='profile'" class="profile-fields"><div><dt>上市日期</dt><dd>{{ detail?.basic?.ipoDate || '—' }}</dd></div><div><dt>{{ detail?.code_change ? '旧代码停用日期' : '退市日期' }}</dt><dd>{{ detail?.basic?.outDate || '—' }}</dd></div><div><dt>当前状态</dt><dd>{{ detail?.code_change ? '代码变更' : detail?.basic?.status==='1'?'上市':detail?.basic?.status==='0'?'退市':'—' }}</dd></div><div><dt>数据来源</dt><dd>BaoStock</dd></div></dl><p v-if="detailTab==='profile' && detail?.code_change" class="form-note">{{ detail.code_change.effective_date }} 起改为 {{ detail.code_change.successor }}。{{ detail.code_change.note }} <a :href="detail.code_change.source_url" target="_blank" rel="noreferrer">实施公告</a></p><p v-if="detailTab==='dividends' && detail?.collection_only_action_note" class="form-note">{{ detail.collection_only_action_note }}</p>
              <template v-if="detailTab==='financial'"><div class="inline-form"><select v-model="financialCategory"><option v-for="(name,key) in financeNames" :value="key">{{ name }}</option></select><span class="subtle">公告日与报告期分别保存；指标保留源值，比例不自动乘 100；空值表示缺失</span></div><div v-if="selectedFinancial.length" class="table-scroll"><table><thead><tr><th v-for="field in rowFields(selectedFinancial)" :key="field">{{ fieldLabel(field) }}</th></tr></thead><tbody><tr v-for="(row,i) in selectedFinancial" :key="i"><td v-for="field in rowFields(selectedFinancial)">{{ sourceCell(row[field]) }}</td></tr></tbody></table></div><p v-else class="empty">此类指标尚未采集，可在数据中心选择对应接口。</p></template>
              <template v-if="detailTab==='dividends'"><p class="form-note" style="padding:12px 20px">保留原始方案阶段；同一派息可能有多条源记录，回测仅合并经济条款一致的同一行动。</p><div v-if="detail?.dividends?.length" class="table-scroll"><table><thead><tr><th v-for="field in ['dividPlanAnnounceDate','dividPlanDate','dividRegistDate','dividOperateDate','dividPayDate','dividCashPsBeforeTax','dividStocksPs','dividReserveToStockPs']">{{ fieldLabel(field) }}</th></tr></thead><tbody><tr v-for="(row,i) in detail.dividends" :key="i"><td v-for="field in ['dividPlanAnnounceDate','dividPlanDate','dividRegistDate','dividOperateDate','dividPayDate','dividCashPsBeforeTax','dividStocksPs','dividReserveToStockPs']">{{ sourceCell(row[field]) }}</td></tr></tbody></table></div><p v-else class="empty">暂无已入库分红事件，不能据此推断从未分红。</p><div v-if="detail?.official_actions?.length" style="padding:12px 20px"><h3>正式公告补充权益</h3><p class="form-note">以下条款存于本地数据库，来源为基金管理人公告，独立于 BaoStock 分红返回。</p><p v-for="event in detail.official_actions" :key="event.sha256">每份现金 {{ event.dividCashPsBeforeTax }} 元 · 登记 {{ event.dividRegistDate }} · 除息 {{ event.dividOperateDate }} · 派息 {{ event.dividPayDate }}。<a :href="event.source" target="_blank" rel="noreferrer">查看正式公告</a></p></div></template>
            </section>
          </div><div v-else class="panel empty">选择证券查看资料和行情。</div>
        </div>
      </template>

      <template v-if="tab === 'research'">
        <div class="research-layout"><section class="panel config-panel"><div class="panel-title"><h2>新建回测</h2><Settings2 :size="16" /></div><form class="form" @submit.prevent="action(submitRun)"><label>策略版本<select v-model="activeStrategy"><option v-for="s in strategies.filter(s=>s.enabled)" :key="s.digest" :value="s.digest">{{ s.manifest.name || s.id }} · {{ s.version }}</option></select></label><label>证券池<textarea v-model="form.codes" rows="2" placeholder="sh.600000,sz.000001"></textarea></label><p class="form-note">支持沪深主板、科创板及创业板 A 股；两板规则区间 {{ health?.backtest_assets?.growth_start }} 至 {{ health?.backtest_assets?.growth_end }}。同时支持已核验的 {{ Object.keys(health?.backtest_assets?.etf_codes || {}).join(' / ') || 'ETF 样例' }} 国内股票 ETF；ETF 区间 {{ health?.backtest_assets?.etf_start }} 至 {{ health?.backtest_assets?.etf_end }}。固定证券池有选择偏差，报告保留此标记。</p><div class="form-pair"><label>开始日期<input type="date" v-model="form.start" min="2023-01-01" required /></label><label>结束日期<input type="date" v-model="form.end" required /></label></div><label>初始资金（元）<input type="number" v-model.number="form.capital" min="1000" required /></label><label>基准代码<input v-model="form.benchmark" required /></label><div class="form-section">模型参数</div><div class="form-pair"><label v-for="(spec,key) in chosenStrategy?.manifest.parameters || {}" :key="key">{{ spec.label || key }}<select v-if="spec.enum" v-model="modelParameters[key]"><option v-for="choice in spec.enum" :value="choice">{{ choice }}</option></select><input v-else-if="spec.type==='boolean'" type="checkbox" v-model="modelParameters[key]" /><input v-else-if="['number','integer'].includes(spec.type)" type="number" v-model.number="modelParameters[key]" :min="spec.minimum" :max="spec.maximum" :step="spec.type==='integer'?1:'any'" required /><input v-else v-model="modelParameters[key]" /></label></div><details><summary>费用与成交假设</summary><div class="form-pair"><label>佣金费率<input type="number" v-model.number="form.commission_rate" step="0.0001" min="0" /></label><label>最低佣金（元）<input type="number" v-model.number="form.minimum_commission" min="0" /></label><label>股票过户费率<input type="number" v-model.number="form.transfer_rate" step="0.00001" min="0" /></label><label>滑点比例<input type="number" v-model.number="form.slippage" step="0.0001" min="0" /></label><label>{{ ['5','15','30','60'].includes(chosenStrategy?.manifest.frequency)?'前一bar量参与上限':'前日量参与上限' }}<input type="number" v-model.number="form.participation" step="0.01" min="0.01" max="1" /></label><label>股票分红固定税率<input type="number" v-model.number="form.dividend_tax" step="0.05" min="0" max="0.2" /></label></div><p class="form-note">股票印花税按历史日期处理，股息税为固定假设。已核验 ETF 无印花税/二级过户费，现金分红按公告免税；佣金含规费，最低额按用户合同配置。</p></details><button class="button primary full" :disabled="busy || !chosenStrategy"><Play :size="15" />运行{{ frequencyNames[chosenStrategy?.manifest.frequency] || '日线' }}信号回测</button></form></section>
          <div class="research-main"><section ref="reportPanel" class="panel"><div class="panel-title"><h2>回测报告</h2><button v-if="report?.manifest.engine_source" class="button" :disabled="busy" @click="action(async()=>{const value=await api('/backtests/'+report.id+'/replay','POST');await refresh();await showReport(value.job_id)})">按原版本重算</button><a v-if="report" class="button" :href="'/api/backtests/'+report.id+'/export'"><ArrowDownToLine :size="14" />导出运行清单</a></div><template v-if="reportMetrics"><p class="form-note">{{ report.manifest.strategy.manifest.name || report.manifest.config.strategy_id }} · {{ report.manifest.config.start }} → {{ report.manifest.config.end }}</p><p v-if="report.manifest.validation_note" class="form-note">{{ report.manifest.validation_note }}</p><div class="metric-strip"><div v-for="item in [{label:'总收益',value:percent(reportMetrics.total_return),color:reportMetrics.total_return},{label:'年化收益',value:percent(reportMetrics.annual_return),color:reportMetrics.annual_return},{label:reportIsMinute?'最大回撤（分钟收盘）':'最大回撤',value:percent(reportMetrics.max_drawdown)},{label:reportIsMinute?'Sharpe（日末）':'Sharpe',value:number(reportMetrics.sharpe)},{label:'成交笔数',value:reportMetrics.trade_count},{label:'交易费用',value:number(reportMetrics.fees)}]" :key="item.label"><small>{{ item.label }}</small><strong :class="changeClass('color' in item ? item.color : undefined)">{{ item.value }}</strong></div></div><div v-if="reportMetrics.benchmark_return!==undefined" class="chart-foot"><span>基准收益 {{ percent(reportMetrics.benchmark_return) }} · 超额收益 {{ percent(reportMetrics.excess_return) }}</span><span>超额为总收益百分点差；价格指数不含分红</span></div><div class="inline-form dataset-form"><label>报告曲线<select v-model="reportCurve" aria-label="报告曲线"><option value="equity">日末净值与基准</option><option value="drawdown">日末回撤</option><option v-if="reportIsMinute" value="intraday_equity">分钟收盘净值</option><option v-if="reportIsMinute" value="intraday_drawdown">分钟收盘回撤</option></select></label><span class="subtle">回撤包含初始资金；分钟最大回撤取分钟收盘，Sharpe/年化按日末</span></div><div class="inline-form dataset-form"><label>交易图证券<select v-model="tradeChartCode" aria-label="交易图证券"><option value="">{{ report.manifest.config.codes[0] }}</option><option v-for="code in report.manifest.config.codes.slice(1)" :key="code" :value="code">{{ code }}</option></select></label><button class="button" :disabled="busy" @click="action(showTradeChart)">在行情页查看买卖点</button></div><p v-if="reportResult.minute_validation?.close_differences?.length" class="form-note">ETF 旧收盘规则：分钟末价与官方日收盘价分别保留，分钟曲线与日末估值可能不同；差异明细随报告导出。</p><p v-if="reportResult.minute_validation?.range_differences?.length" class="form-note">来源分钟极值与日线有差异，详见成交假设；本报告不验证触价或止损成交。</p><p v-if="reportResult.equity.length<20" class="form-note">本次仅 {{ reportResult.equity.length }} 个交易日，年化收益和 Sharpe 为短区间外推，不代表长期表现。</p><Chart :option="equityOptions" /><div class="chart-foot">数据版本 {{ reportResult.data_version }} · {{ frequencyNames[reportResult.signal_frequency || 'd'] }}信号 · {{ reportIsMinute?'下一bar开盘成交':'日线开盘成交' }} · {{ curveNames[reportCurve] }} · 252 个交易日年化<span>基准：{{ report.manifest.config.benchmark }}（价格指数）</span></div><p v-if="reportResult.universe_input_note" class="form-note">{{ reportResult.universe_input_note }}</p><details v-if="reportEtfs.length" class="report-assumptions"><summary>ETF 产品规则与公告权益</summary><p v-for="[code,rule] in reportEtfs" :key="code">{{ code }} · {{ rule.category }} · {{ rule.tick }} 元报价 · 百份买入 · {{ rule.settlement }} · 单笔最多 {{ rule.max_order_quantity }} 份；已核验来源截至 {{ rule.coverage.end }}。</p><p v-for="event in reportEtfActions" :key="event.sha256">{{ event.code }} 每份现金 {{ event.dividCashPsBeforeTax }} 元 · 登记 {{ event.dividRegistDate }} · 除息 {{ event.dividOperateDate }} · 派息 {{ event.dividPayDate }} · 税率 0。<a :href="event.source" target="_blank" rel="noreferrer">管理人正式公告</a><small>原件 SHA256：{{ event.sha256 }}</small></p><p>正式公告补充 BaoStock 缺失的权益。只核对已知公告和来源参考价，未知事件继续停止，不能推断所有 ETF 历史完整。</p></details><details v-if="reportGrowth.length" class="report-assumptions"><summary>科创板 / 创业板规则与更正权益</summary><p v-for="[code,rule] in reportGrowth" :key="code">{{ code }} · {{ rule.board==='star'?'科创板':'创业板' }} · 买入最低 {{ rule.buy_minimum }} 股、增量 {{ rule.lot }} 股 · {{ rule.tick }} 元报价 · 普通/ST 涨跌幅 20% · {{ rule.settlement }} · 单笔限价数量上限 {{ rule.max_order_quantity }} 股。{{ rule.order_policy }}</p><p v-for="event in reportCorrections" :key="event.code+event.dividOperateDate">{{ event.code }} {{ event.dividOperateDate }}：保留 {{ event.source_variants }} 条源版本，仅采用 {{ event.verified_announcement_date }} 更正版，每股税前 {{ event.dividCashPsBeforeTax }} 元；原版不另派现金。正式来源与原始版本随运行清单导出。</p><p>IPO 前五日按上市日期及完整交易日历计数；特殊退市与盘口机制未由 OHLC 重建。规则版本、正式原件摘要、零股及部分成交政策见成交假设。</p></details><details class="report-assumptions"><summary>查看成交假设与复现信息</summary><ul><li v-for="text in report.manifest.assumptions" :key="text">{{ text }}</li></ul><p>{{ reportResult.cash_policy }}</p><p v-for="text in [...(reportMetrics.notes || []),...(reportResult.warnings || [])]" :key="text">{{ text }}</p><p>引擎 {{ report.manifest.engine_version }} · 模型 {{ report.manifest.strategy.version }} · 摘要 {{ report.manifest.strategy.digest.slice(0,16) }}</p></details><div class="inline-form dataset-form"><select v-model="reportSection" aria-label="报告明细" @change="reportPage=1"><option value="trades">成交记录</option><option value="orders">全部订单与未成交原因</option><option value="cashflows">公司行动现金流</option><option value="equity">每日现金与持仓</option><option v-if="reportIsMinute" value="intraday_equity">分钟现金与持仓</option><option v-if="reportResult.signal_events" value="signal_events">信号时点与估值日期</option><option v-if="reportResult.financial_reports" value="financial_reports">策略可见财报与公告日期</option><option v-if="reportResult.universe_snapshots" value="universe_snapshots">策略可见历史池与来源摘要</option></select><span class="subtle">{{ reportRows.length }} 条 · 原始细节均包含在导出清单中</span></div><div class="table-scroll trade-table"><table><thead><tr><th v-for="field in reportColumns">{{ reportSection==='financial_reports' && field==='period'?'报告期':reportSection==='universe_snapshots' && field==='digest'?'来源池内容摘要':reportLabels[field] }}</th></tr></thead><tbody><tr v-for="(row,i) in reportRows.slice((reportPage-1)*100,reportPage*100)" :key="i"><td v-for="field in reportColumns">{{ reportCell(row,field) }}</td></tr></tbody></table></div><div class="pagination"><span>{{ reportPage }} / {{ Math.max(1,Math.ceil(reportRows.length/100)) }}</span><button :disabled="reportPage<=1" aria-label="明细上一页" @click="reportPage--"><ChevronLeft :size="16" /></button><button :disabled="reportPage*100>=reportRows.length" aria-label="明细下一页" @click="reportPage++"><ChevronRight :size="16" /></button></div></template><div v-else class="empty report-empty"><Activity :size="34" /><h3>{{ report ? statusNames[report.status] : '从一个明确的交易假设开始' }}</h3><p>{{ report?.error || '选择模型、日期和证券池，运行后查看净值、成交与完整假设。' }}</p><p v-if="report?.manifest.validation_note">{{ report.manifest.validation_note }}</p><p v-if="report" class="mono">{{ report.id }}</p></div></section>
            <section class="panel"><div class="panel-title"><h2>历史运行</h2><button class="button" :disabled="compared.length<2" @click="action(compareRuns)"><Layers3 :size="14" />对比净值</button></div><div class="table-scroll"><table><thead><tr><th></th><th>运行 / 模型</th><th>回测区间</th><th>状态</th><th>总收益</th><th>数据版本</th><th></th></tr></thead><tbody><tr v-for="run in runs" :key="run.id"><td><input v-model="compared" type="checkbox" :value="run.id" :disabled="run.status!=='completed'" aria-label="选择运行对比" /></td><td><strong>{{ run.config.strategy_id }}</strong><small class="mono">{{ run.id.slice(0,8) }}</small></td><td>{{ run.config.start }} → {{ run.config.end }}</td><td><span class="status" :class="run.status">{{ statusNames[run.status] }}</span></td><td :class="changeClass(run.metrics?.total_return)">{{ percent(run.metrics?.total_return) }}</td><td>{{ run.data_version }}</td><td><button class="text-button" @click="action(()=>showReport(run.id))">查看<ArrowUpRight :size="12" /></button></td></tr></tbody></table><div v-if="!runs.length" class="empty">此页没有回测运行</div></div><div class="pagination"><span>共 {{ runTotal }} 条 · 第 {{ runPage }} / {{ Math.max(1,Math.ceil(runTotal/20)) }} 页</span><button class="button" :disabled="busy || runPage===1" @click="action(async()=>{runPage--;await loadRuns()})" aria-label="历史运行上一页"><ChevronLeft :size="14" />上一页</button><button class="button" :disabled="busy || runPage*20>=runTotal" @click="action(async()=>{runPage++;await loadRuns()})" aria-label="历史运行下一页">下一页<ChevronRight :size="14" /></button></div><Chart v-if="comparison.length" :option="comparisonOptions" /></section><section class="panel"><div class="panel-title"><h2>分段参数研究</h2><span class="tag">训练 → 验证 → 锁定 → 样本外</span></div><details class="research-editor"><summary>创建一项参数研究</summary><div class="form"><label>研究名称<input v-model="researchName" maxlength="100" /></label><div class="form-pair" v-for="(interval,key) in researchIntervals" :key="key"><label>{{ stageNames[key] }}开始<input v-model="interval.start" type="date" /></label><label>{{ stageNames[key] }}结束<input v-model="interval.end" type="date" /></label></div><label>候选参数（JSON 数组，每项一组）<textarea v-model="candidateText" rows="5" placeholder='[{"fast":10,"slow":30,"exposure":0.95}]' /></label><button class="button" @click="candidateText=JSON.stringify([modelParameters],null,2)">填入当前模型参数</button><p class="form-note">使用左侧模型、股票池、资金及费用配置。三个区间依次排列且不重叠，每段独立初始资金、不继承持仓。一次最多 20 组；先运行全部训练与验证，再选择一组锁定并运行样本外。重新查看已知历史不能证明它从未被用于调参。</p><button class="button primary" :disabled="busy" @click="action(createResearch)">创建训练与验证任务</button></div></details><div class="inline-form dataset-form"><select v-model="researchId" aria-label="选择参数研究" @change="action(loadResearch)"><option value="">选择已保存研究</option><option v-for="item in researchList" :key="item.id" :value="item.id">{{ item.name }} · {{ item.id.slice(0,8) }}</option></select><button class="button" @click="action(async()=>{researchList=await api('/experiments');await loadResearch()})">刷新研究</button></div><template v-if="research"><p class="research-caption">数据版本 {{ research.data_version }} · 引擎 {{ research.engine_version }} · {{ research.selected===null?'尚未锁定参数':'已锁定候选 '+(research.selected+1) }}</p><p class="research-caption" v-for="(interval,key) in research.intervals" :key="key">{{ stageNames[key] }}：{{ interval.start }} → {{ interval.end }}</p><div class="table-scroll"><table><thead><tr><th>候选 / 参数</th><th>区间</th><th>状态</th><th>总收益</th><th>最大回撤</th><th></th></tr></thead><tbody><tr v-for="row in research.runs" :key="row.run_id"><td>{{ row.candidate+1 }} · {{ JSON.stringify(research.candidates[row.candidate]) }}</td><td>{{ stageNames[row.stage] }}</td><td><span class="status" :class="row.status">{{ statusNames[row.status] }}</span><small v-if="row.error">{{ row.error }}</small></td><td :class="changeClass(row.metrics?.total_return)">{{ percent(row.metrics?.total_return) }}</td><td>{{ percent(row.metrics?.max_drawdown) }}</td><td><button class="text-button" @click="action(()=>showReport(row.run_id))">报告</button><button v-if="row.stage==='validation' && research.selected===null" class="button" :disabled="busy || !researchReady" @click="action(()=>lockResearch(row.candidate))">锁定此组并运行样本外</button></td></tr></tbody></table></div></template></section>
          </div></div>
      </template>

      <template v-if="tab==='strategies'">
        <section class="panel install-panel"><div><h2>安装本地 Python 模型</h2><p>受信任的目录需包含 manifest.json 和 Python 入口。安装只校验并保存版本；运行时在独立进程加载。</p></div><form class="inline-form" @submit.prevent="action(installStrategy,'模型已安装，版本已固化。')"><input v-model="installedPath" placeholder="输入本机策略包的完整目录路径" required /><button class="button primary" :disabled="busy"><FolderPlus :size="15" />安装模型</button></form><p class="form-note">独立进程用于故障隔离。请安装自己编写或了解来源的代码。</p></section>
        <div class="strategy-grid"><article v-for="strategy in strategies" :key="strategy.digest" class="panel strategy-card"><div class="strategy-icon"><Layers3 :size="25" /></div><div class="card-heading"><h2>{{ strategy.manifest.name || strategy.id }}</h2><span class="tag">{{ strategy.enabled?'已启用':'已停用' }}</span></div><p>{{ strategy.manifest.description }}</p><dl><div><dt>版本</dt><dd>{{ strategy.version }}</dd></div><div><dt>频率</dt><dd>{{ frequencyNames[strategy.manifest.frequency] || strategy.manifest.frequency }}</dd></div><div><dt>预热</dt><dd>{{ strategy.manifest.lookback }} 根</dd></div></dl><div class="card-foot"><code>{{ strategy.digest.slice(0,16) }}</code><button class="text-button" @click="action(()=>toggleStrategy(strategy))">{{ strategy.enabled?'停用':'启用' }}</button><button class="button" :disabled="!strategy.enabled" @click="activeStrategy=strategy.digest; tab='research'">配置回测<ArrowUpRight :size="13" /></button></div></article></div>
      </template>

      <template v-if="tab==='data'">
        <div class="data-summary"><div class="panel"><Database :size="21" /><span><small>本地存储</small><strong>{{ health?.database || '未连接' }}</strong></span></div><div class="panel"><Layers3 :size="21" /><span><small>官方数据方法</small><strong>{{ catalogue.length }} 个</strong></span></div><div class="panel"><Activity :size="21" /><span><small>今日已记请求</small><strong>{{ overview.usage[0]?.requests || 0 }} <em>/ 45,000 预算</em></strong></span></div></div>
        <div class="data-layout"><section class="panel"><div class="panel-title"><h2>行情与参考数据采集</h2><span class="tag">串行通道</span></div><form class="form" @submit.prevent="action(submitCollection)"><label>证券代码<textarea v-model="collect.codes" rows="2" required></textarea></label><div class="form-pair"><label>起始日期<input type="date" v-model="collect.start" /></label><label>截止日期<input type="date" v-model="collect.end" /></label></div><label>行情频率<select v-model="collect.frequency"><option value="d">日线</option><option value="w">周线</option><option value="m">月线</option><option v-for="f in ['5','15','30','60']" :value="f">{{ f }} 分钟</option></select></label><p class="form-note">同时更新证券资料、交易日历及所选股票的分红/因子。指数没有分钟线。未发布批次不会进入页面或回测。</p><button class="button primary" :disabled="busy"><RefreshCw :size="14" />提交采集任务</button></form></section>
          <section class="panel"><div class="panel-title"><h2>接口采集</h2><span class="subtle">财务 / 行业 / 指数成分 / 宏观</span></div><form class="form" @submit.prevent="action(submitOperation)"><label>官方方法<select v-model="method"><option v-for="endpoint in catalogue" :key="endpoint.method" :value="endpoint.method">{{ endpoint.label }} · {{ endpoint.method }}</option></select></label><code class="signature">{{ selectedEndpoint?.signature }}</code><label>参数（JSON）<textarea v-model="argumentsText" rows="5" class="mono" spellcheck="false"></textarea></label><div class="inline-form"><button class="button" :disabled="busy">提交接口采集</button><a :href="selectedEndpoint?.url" target="_blank" rel="noreferrer" class="text-button">官方字段说明<ArrowUpRight :size="13" /></a></div></form></section></div>
        <section class="panel"><div class="panel-title"><h2>本机定时同步</h2><span class="subtle">Asia/Shanghai · 后台工作进程停止期间暂停，重启按水位补跑</span></div><form class="form" @submit.prevent="action(saveSync)"><label><span><input type="checkbox" v-model="sync.enabled" /> 启用定时更新</span></label><label>公司行动 / 财务 / 多周期证券范围<textarea v-model="syncCodes" rows="2"></textarea></label><div class="list-filters"><label v-for="f in ['w','m']"><input type="checkbox" :value="f" v-model="sync.periods" />{{ f==='w'?'周线':'月线' }}</label><label v-for="f in ['5','15','30','60']"><input type="checkbox" :value="f" v-model="sync.minutes" />{{ f }} 分钟</label></div><p class="form-note">A 股 / ETF 日线与主要指数按就绪日更新；参考资料每日、因子 18:10 后、财务次日 01:40 后、分钟 20:10 后、周线周六、月线次月首日。所选证券与自选合并；日线、证券池、指数及分钟按各自水位从最早欠缺日起分批补采。首次无水位仅取最新就绪日，更早历史用区间回填。达到本机预算后次日自动续跑；接口失败保留日志供手动重跑。</p><button class="button" :disabled="busy">保存同步设置</button><button type="button" class="button" :disabled="busy" @click="action(async()=>{const value=await api('/backfill','POST',{start:collect.start,end:collect.end,asset:'stock'});notice='已入队 '+value.codes+' 只股票的日线回填；'+value.note;await refresh();await focusJob(value.job_id)})">仅回填结束日 A 股名单</button><button type="button" class="button" :disabled="busy" @click="action(async()=>{const value=await api('/backfill','POST',{start:collect.start,end:collect.end,asset:'stock',pool_mode:'historical'});notice='已入队 '+value.codes+' 只股票的日线回填；'+value.note;await refresh();await focusJob(value.job_id)})">按历史成员回填 A 股日线</button><p class="form-note">历史成员模式要求区间每个交易日都有完整证券池快照，包含后来退市的成员。结束日模式只回填该日名单，不代表多年全市场完整；资产类型按固定版本档案；缺档案仅使用已核验的交易所精确代码分类并随任务说明，其他未知成员仍停止。ETF 可通过本机 API 指定 asset=etf。</p></form></section>
        <section class="panel"><div class="panel-title"><h2>历史证券池</h2><span class="subtle">来源回溯成员 / 交易状态</span></div>
          <div class="inline-form dataset-form"><button class="button" :disabled="busy" @click="action(checkUniverseCoverage)">检查采集区间证券池覆盖</button><button class="button" :disabled="busy" @click="action(fillUniverse)">回填历史证券池</button></div>
          <p v-if="universeCoverage" class="research-caption">{{ universeCoverage.start }} → {{ universeCoverage.end }}：{{ universeCoverage.published_days }} / {{ universeCoverage.trading_days }} 个交易日已发布；{{ universeCoverage.complete?'区间快照齐全':'缺少 '+universeCoverage.missing_days.length+' 日，前几个：'+universeCoverage.missing_days.slice(0,5).join('、') }}。数据版本 {{ universeCoverage.version }}；不表示行情已完整。</p>
          <form class="inline-form dataset-form" @submit.prevent="universePage=1; universeData={data:[],total:0}; action(loadUniverse)"><label>证券池日期<input type="date" v-model="universeDay" /></label><input v-model="universeQuery" placeholder="返回名称或证券代码" aria-label="返回名称或证券代码" /><button class="button" :disabled="busy">读取当日证券池</button><a v-if="universeData.day" class="button" :href="universeExportUrl">导出当日完整 CSV</a></form>
          <template v-if="universeData.day"><p class="research-caption">{{ universeData.day }} · {{ universeData.members }} 个来源成员 · 匹配 {{ universeData.total }} 个 · 快照批次 {{ universeData.source_batch }} · 数据版本 {{ universeData.version }}。<span v-if="universeData.missing_name_count">名称缺失 {{ universeData.missing_name_count }} 个。</span>{{ universeData.note }}</p><div class="table-scroll"><table><thead><tr><th>证券代码</th><th>来源回溯名称</th><th>来源交易状态</th></tr></thead><tbody><tr v-for="row in universeData.data" :key="row.code"><td>{{ row.code }}</td><td>{{ row.code_name || '来源未提供名称' }}</td><td>{{ row.tradeStatus==='1'?'交易':'停牌' }}</td></tr></tbody></table></div><div class="pagination"><span>{{ universePage }} / {{ Math.max(1,Math.ceil(universeData.total/100)) }}</span><button :disabled="universePage<=1 || busy" aria-label="证券池上一页" @click="universePage--; action(loadUniverse)"><ChevronLeft :size="16" /></button><button :disabled="universePage*100>=universeData.total || busy" aria-label="证券池下一页" @click="universePage++; action(loadUniverse)"><ChevronRight :size="16" /></button></div></template><p v-else class="empty">按明确交易日读取；缺少快照时停止，不借用当前或相邻日名单。</p>
        </section>
        <section class="panel"><div class="panel-title"><h2>采集与回测任务</h2><span class="subtle">持久化队列 · 检查点与错误可追踪</span></div><div class="table-scroll"><table><thead><tr><th>任务 ID</th><th>类型</th><th>状态</th><th>进度</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="job in jobs" :key="job.id"><td class="mono"><button class="text-button" @click="action(()=>focusJob(job.id))">{{ job.id.slice(0,8) }}</button></td><td>{{ job.kind==='collect'?'数据采集':'策略回测' }}</td><td><span class="status" :class="job.status">{{ statusNames[job.status] }}</span></td><td>{{ job.progress }} / {{ job.total || '—' }}<progress v-if="job.total" :value="job.progress" :max="job.total"></progress></td><td>{{ dateTime(job.created_at) }}</td><td><button v-if="['queued','running','waiting'].includes(job.status)" class="text-button" @click="action(async()=>{await api('/jobs/'+job.id+'/cancel','POST');await refresh()})">取消</button><button v-if="['failed','cancelled','interrupted'].includes(job.status)" class="text-button" @click="action(async()=>{await api('/jobs/'+job.id+'/retry','POST');await refresh()})">按检查点重跑</button><button class="text-button" @click="action(()=>focusJob(job.id))">日志</button></td></tr></tbody></table></div><div class="pagination"><span>{{ activeJobs }} 个活动任务始终显示 · 历史 {{ jobTotal }} 条 · 第 {{ jobPage }} / {{ Math.max(1,Math.ceil(jobTotal/20)) }} 页</span><button class="button" :disabled="busy || jobPage===1" @click="action(async()=>{jobPage--;await loadJobs()})" aria-label="历史任务上一页"><ChevronLeft :size="14" />上一页</button><button class="button" :disabled="busy || jobPage*20>=jobTotal" @click="action(async()=>{jobPage++;await loadJobs()})" aria-label="历史任务下一页">下一页<ChevronRight :size="14" /></button></div><div v-if="focusedJob" class="job-log"><div><strong>{{ focusedJob.id }}</strong><span>{{ focusedJob.error }}</span><button class="icon-button" aria-label="关闭日志" @click="focusedJob=null"><X :size="14" /></button></div><p v-for="(log,i) in focusedJob.logs" :key="i"><time>{{ dateTime(log.created_at) }}</time><span :class="{'up':log.level==='error'}">{{ log.message }}</span></p></div><div v-if="focusedJob" class="pagination"><span>日志 {{ focusedJob.logs_total }} 条 · 从新到旧 · 第 {{ logPage }} / {{ Math.max(1,Math.ceil(focusedJob.logs_total/100)) }} 页</span><button class="button" :disabled="busy || logPage===1" @click="action(async()=>{logPage--;await focusJob(focusedJob.id)})" aria-label="任务日志上一页"><ChevronLeft :size="14" />上一页</button><button class="button" :disabled="busy || logPage*100>=focusedJob.logs_total" @click="action(async()=>{logPage++;await focusJob(focusedJob.id)})" aria-label="任务日志下一页">下一页<ChevronRight :size="14" /></button></div></section>
        <section class="panel"><div class="panel-title"><h2>官方接口覆盖目录</h2><span class="subtle">已返回样本不等于全部历史完整</span></div><p class="research-caption">采集非空发布只证明本地取得样本。覆盖按数据集累计来源修订与末期统计，多方法可能共用数据集；不是去重行情数，也不是全区间完成。历史用途、源不支持与待验证范围分别列明。</p><div class="table-scroll"><table class="interface-matrix"><colgroup><col v-for="(width,i) in ['7%','18%','7%','13%','7%','20%','12%','16%']" :key="i" :style="{width}" /></colgroup><thead><tr><th>数据族</th><th>方法</th><th>本地数据集</th><th>采集证据</th><th>最新返回行数</th><th>来源覆盖</th><th>历史读取方式</th><th>说明与边界</th></tr></thead><tbody><tr v-for="endpoint in catalogue" :key="endpoint.method"><td>{{ endpoint.label }}</td><td><a :href="endpoint.url" target="_blank" rel="noreferrer" class="mono">{{ endpoint.method }}</a></td><td>{{ endpoint.dataset }}</td><td><span class="status" :class="endpoint.capabilities.collection_status==='verified'?'completed':endpoint.capabilities.collection_status==='partial'?'waiting':'queued'">{{ endpoint.capabilities.collection_label }}</span><small>{{ endpoint.capabilities.collection_evidence.published_batches }} 个发布批次 · {{ endpoint.capabilities.collection_evidence.nonempty_batches }} 个非空批次</small></td><td>{{ endpoint.verification?.row_count ?? '—' }}</td><td><span class="status" :class="endpoint.capabilities.coverage_status==='partial'?'waiting':'queued'">{{ endpoint.capabilities.coverage_label }}</span><small v-for="item in endpoint.capabilities.source_coverage" :key="item.dataset">{{ item.dataset }} · {{ number(item.revisions,0) }} 个源修订 · 末期 {{ item.latest || '无期间字段' }}</small></td><td>{{ endpoint.capabilities.strategy_access }}</td><td><details><summary>查看支持范围</summary><p>{{ endpoint.capabilities.scope }}</p><p v-if="endpoint.historical_support">{{ endpoint.historical_support.reason }}</p><p v-if="endpoint.capabilities.source_unsupported.length">源不支持：{{ endpoint.capabilities.source_unsupported.join('、') }}</p><p v-if="endpoint.capabilities.pending_verification.length">待验证：{{ endpoint.capabilities.pending_verification.join('、') }}</p><p>{{ endpoint.historical_note }}</p></details></td></tr></tbody></table></div></section>
        <section class="panel"><div class="panel-title"><h2>入库数据浏览</h2><span class="subtle">保留来源字段，单页最多 100 行</span></div><form class="inline-form dataset-form" @submit.prevent="datasetPage=1; action(loadDataset)"><select v-model="dataset" aria-label="入库数据集" @change="resetDataset(); if (['deposit_rate','loan_rate','reserve_ratio','money_month','money_year'].includes(dataset)) datasetCode=''"><option v-for="name in ['official_actions','industry','sz50','hs300','zz500',...Object.keys(financeNames),'deposit_rate','loan_rate','reserve_ratio','money_month','money_year']" :value="name">{{ financeNames[name] || ({official_actions:'ETF 公告权益（补充来源）',industry:'行业',sz50:'上证 50 成分',hs300:'沪深 300 成分',zz500:'中证 500 成分',deposit_rate:'存款利率',loan_rate:'贷款利率',reserve_ratio:'准备金率',money_month:'月货币供应量',money_year:'年货币供应量'} as Record<string,string>)[name] || name }}</option></select><input v-model="datasetCode" placeholder="证券代码，宏观数据留空" @input="resetDataset" /><button class="button">读取本地数据</button><a class="button" :href="datasetExportUrl">导出全部 CSV</a><span class="subtle">{{ datasetData.total }} 行</span></form><p class="research-caption">按官方字段说明显示中文名称，保留来源原值；不同接口的百分数与小数比例口径可能不同，未明确的单位不猜测转换。</p><div v-if="datasetData.historical_support" class="macro-support"><h3>{{ datasetData.historical_support.label }}</h3><p>{{ datasetData.historical_support.reason }}</p><p class="subtle">固定数据版本 {{ datasetData.version }} · {{ datasetData.historical_support.rows }} 条来源记录 · 来源末期 {{ datasetData.historical_support.latest_period || '未提供' }}<template v-if="datasetData.historical_support.latest_effectiveDate"> · 最后执行日期 {{ datasetData.historical_support.latest_effectiveDate }}</template> · 日期无效/缺失字段 {{ datasetData.historical_support.invalid_clock_fields }}</p><p class="subtle">{{ datasetData.historical_support.note }}</p><details class="detail-fold"><summary>本版本全部记录的数值覆盖</summary><div class="table-scroll"><table><thead><tr><th>来源字段</th><th>有效数值</th><th>空值</th><th>非法数值</th><th>有效零值</th><th>最近有效数值期</th></tr></thead><tbody><tr v-for="(counts,field) in datasetData.historical_support.values" :key="field"><td>{{ fieldLabel(String(field)) }}<small>{{ field }}</small></td><td>{{ counts.valid }}</td><td>{{ counts.missing }}</td><td>{{ counts.invalid }}</td><td>{{ counts.zero }}</td><td>{{ counts.latest_valid_period || '无有效数值' }}</td></tr></tbody></table></div></details></div><details v-if="datasetData.data.length && dataset!=='official_actions'" class="detail-fold"><summary>字段说明与来源</summary><div class="table-scroll"><table><thead><tr><th>来源字段</th><th>中文名称</th><th>单位 / 数值口径</th><th>含义</th></tr></thead><tbody><tr v-for="field in rowFields(datasetData.data)" :key="field"><td><code>{{ field }}</code></td><td>{{ fieldLabel(field) }}</td><td>{{ fieldUnit(field) }}</td><td>{{ fieldDescription(field) }}</td></tr></tbody></table></div><a v-if="catalogue.find(endpoint=>endpoint.dataset===dataset)?.url" :href="catalogue.find(endpoint=>endpoint.dataset===dataset)?.url" target="_blank" rel="noreferrer" class="text-button">查看该接口官方字段说明</a></details><div v-if="datasetData.data.length" class="table-scroll"><table><thead><tr><th v-for="field in rowFields(datasetData.data)" :title="fieldDescription(field)">{{ fieldLabel(field,dataset==='official_actions') }}</th></tr></thead><tbody><tr v-for="(row,i) in datasetData.data" :key="i"><td v-for="field in rowFields(datasetData.data)">{{ sourceCell(row[field]) }}</td></tr></tbody></table></div><div v-else class="empty">选择数据集和证券后读取，未采集的数据不会显示为零。</div><div class="pagination"><span>{{ datasetPage }} / {{ Math.max(1,Math.ceil(datasetData.total/100)) }} · 数据版本 {{ datasetData.version || '—' }}</span><button :disabled="datasetPage<=1" aria-label="数据上一页" @click="datasetPage--; action(loadDataset)"><ChevronLeft :size="16" /></button><button :disabled="datasetPage*100>=datasetData.total" aria-label="数据下一页" @click="datasetPage++; action(loadDataset)"><ChevronRight :size="16" /></button></div></section>
      </template>
    </main><footer><span><i></i>数据保存在本机 · PostgreSQL</span><span>BaoStock 历史数据 · 不提供实时行情 / 实盘下单</span><span>阿雷量化 / 研究工作台</span></footer>
  </div>
</template>
