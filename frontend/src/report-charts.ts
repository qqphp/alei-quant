type Bar = { date: string; time?: string; display_scale: number }
type Trade = { date: string; execution_at?: string; side: string; price: number; quantity: number }

export function drawdowns(points: { value: number }[]) {
  let peak = 1
  return points.map(point => {
    peak = Math.max(peak, point.value)
    return point.value / peak - 1
  })
}

// 来源分钟 K 线以结束时间命名，成交时刻位于对应 K 线开盘。
export function tradeMarkers(bars: Bar[], trades: Trade[], frequency: string) {
  const minutes = ['5', '15', '30', '60'].includes(frequency)
  const byOpen = new Map(bars.map((bar, index) => {
    if (!minutes) return [bar.date, index] as const
    const stamp = bar.time || ''
    const open = Number(stamp.slice(8, 10)) * 60 + Number(stamp.slice(10, 12)) - Number(frequency)
    const clock = String(Math.floor(open / 60)).padStart(2, '0') + ':' + String(open % 60).padStart(2, '0')
    return [bar.date + 'T' + clock, index] as const
  }))
  const missing: Trade[] = []
  const points = trades.flatMap(trade => {
    const key = minutes ? trade.execution_at?.slice(0, 16) : trade.date
    const index = key === undefined ? undefined : byOpen.get(key)
    if (index === undefined) { missing.push(trade); return [] }
    const coord: [number, number] = [index, trade.price * bars[index]!.display_scale]
    const buy = trade.side === 'buy'
    return [{ name: buy ? '买入' : '卖出', coord, value: trade.price,
      quantity: trade.quantity, execution: trade.execution_at || trade.date,
      symbol: 'triangle', symbolRotate: buy ? 0 : 180, symbolSize: 12,
      itemStyle: { color: buy ? '#d45d5d' : '#309d87' },
      label: { show: true, formatter: buy ? '买' : '卖', position: (buy ? 'bottom' : 'top') as 'bottom' | 'top', fontSize: 10 },
    }]
  })
  return { points, missing }
}
