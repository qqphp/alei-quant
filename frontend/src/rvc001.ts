import type { SeriesOption } from 'echarts'
import { number } from './api'

type State = 'red' | 'pink' | 'blue' | 'green'
type Indicator = {
  open: number; close: number; low: number; high: number
  fast: number; slow: number; mid: number; sd: number | null
  up: number | null; down: number | null; up1: number | null; down1: number | null
  state: State | null; buy: boolean; down_cross: boolean; red_exit: boolean; pink_exit: boolean; green_exit: boolean
}
export type RvcBar = { open: number; close: number; low: number; high: number; rvc001: Indicator }
export const rvcColors = { red: '#ff0000', pink: '#ff66ff', blue: '#0088ff', green: '#00ff00' }
const stateNames = { red: '红K · 多头增强', pink: '粉K · 多头减弱', blue: '蓝K · 空头减弱', green: '绿K · 空头增强' }
const arrow = 'path://M0,-8L-6,0L-2,0L-2,8L2,8L2,0L6,0Z'
const tracks = ['up', 'down', 'up1', 'down1'] as const

function greenExitName(value: Indicator) {
  return [value.red_exit && value.state === 'green' ? '阿雷红变' : '', value.green_exit ? '阿雷绿变' : ''].filter(Boolean).join('、')
}

export function reportRedExitMode(engineVersion: string) {
  return engineVersion === 'cash-ledger-0.13.0' ? 'pink' : 'pink_or_green'
}

export function rvcSeries(bars: RvcBar[], dates: string[], digits: number): SeriesOption[] {
  const points: any[] = []
  bars.forEach((bar, index) => {
    const value = bar.rvc001
    const add = (name: string, color: string, price: number, down = false, original = false) => points.push({
      name, coord: [index, price], value: price, date: dates[index],
      symbol: original ? 'triangle' : arrow, symbolSize: original ? 7 : 17,
      symbolRotate: down ? 180 : 0, symbolOffset: original ? [13, 0] : [0, 0],
      itemStyle: { color }, label: { show: false },
    })
    if (value.buy) add('红箭头 · 金叉买入信号', rvcColors.red, value.low * 0.985)
    if (value.pink_exit) add('粉箭头 · 阿雷红变出场信号', rvcColors.pink, value.high * 1.015, true)
    if (value.green_exit || (value.red_exit && value.state === 'green')) add(`绿箭头 · ${greenExitName(value)}出场信号`, rvcColors.green, value.high * 1.015, true)
    if (value.down_cross) add('死叉 · 原公式标记', '#dddddd', value.high * 1.015, true, true)
  })
  return [
    { name: 'RVC001', type: 'candlestick', xAxisIndex: 1, yAxisIndex: 1,
      data: bars.map(bar => {
        const value = bar.rvc001, color = value.state ? rvcColors[value.state] : '#888888'
        return { value: [value.open, value.close, value.low, value.high],
          itemStyle: { color, color0: color, borderColor: color, borderColor0: color, borderColorDoji: color } }
      }),
      markPoint: { data: points, tooltip: { trigger: 'item', formatter: (params: any) =>
        `${params.data.date}<br/>${params.data.name}<br/>信号价格 ${number(params.data.value, digits)} 元<br/>日线策略在信号确认后下一交易日执行，实际成交见报告。` } },
    },
    ...tracks.map((field, index) => ({ name: field.toUpperCase(), type: 'line' as const,
      xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, connectNulls: false,
      data: bars.map(bar => bar.rvc001[field]), itemStyle: { color: index < 2 ? '#888888' : '#666666' },
      lineStyle: { width: 1, color: index < 2 ? '#888888' : '#666666' },
    })),
  ]
}

export function rvcTooltip(bar: RvcBar, date: string, digits: number) {
  const value = bar.rvc001
  const ohlc = (item: RvcBar | Indicator) => `开 ${number(item.open, digits)} · 收 ${number(item.close, digits)} · 低 ${number(item.low, digits)} · 高 ${number(item.high, digits)}`
  const signals = [value.buy ? '红箭头：金叉买入' : '', value.pink_exit ? '粉箭头：阿雷红变出场' : '',
    value.green_exit || (value.red_exit && value.state === 'green') ? `绿箭头：${greenExitName(value)}出场` : '', value.down_cross ? '小标记：原公式死叉' : ''].filter(Boolean)
  return `${date}<br/>主图：${ohlc(bar)}<br/>RVC001 · ${value.state ? stateNames[value.state] : '初始K线'}<br/>信号价：${ohlc(value)}<br/>EMA3 ${number(value.fast, 4)} · EMA8 ${number(value.slow, 4)}<br/>` +
    tracks.map(field => `${field.toUpperCase()} ${number(value[field], digits)}`).join(' · ') + `<br/>${signals.join('；') || '无新增信号'}`
}
