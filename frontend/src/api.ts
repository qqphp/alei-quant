export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch('/api' + path, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Stock-Local': '1' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new Error('本机服务暂时未响应，请确认后台运行后重试。')
  }
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail ?? value))
  return value
}

export const number = (value: unknown, digits = 2) => value === '' || value === null || value === undefined ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
export function businessCell(value: unknown, field = '') {
  if (value === '' || value === null || value === undefined) return '—'
  if (/(?:date|time|_at)$/i.test(field) || ['code', 'code_name', 'status', 'type', 'tradeStatus', 'tradestatus', 'isST', 'is_trading_day', 'adjustflag', 'profitForcastType', 'statYear', 'statMonth'].includes(field)) return value
  const numeric = typeof value === 'number' || typeof value === 'string' && /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())
  return numeric && Number.isFinite(Number(value)) ? number(value) : value
}
export const percent = (value: unknown) => value === null || value === undefined ? '—' : number(Number(value) * 100) + '%'
export const dateTime = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

