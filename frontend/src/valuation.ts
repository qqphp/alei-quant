export const valuationFields = [
  { field: 'peTTM', label: 'PE TTM · 滚动市盈率' },
  { field: 'pbMRQ', label: 'PB MRQ · 市净率' },
  { field: 'psTTM', label: 'PS TTM · 滚动市销率' },
  { field: 'pcfNcfTTM', label: 'PCF TTM · 滚动市现率' },
] as const

// 估值取来源日线原值；复权比例只用于价格，不用于这些比值。
export function valuationSeries(bars: Record<string, unknown>[], field: string) {
  const values = bars.map(bar => {
    const value = bar[field]
    if (typeof value !== 'number' && (typeof value !== 'string' || value.trim() === '')) return null
    const number = Number(value)
    return Number.isFinite(number) ? number : null
  })
  return { values, available: values.filter(value => value !== null).length, missing: values.filter(value => value === null).length }
}
