export type TaskDateRange = { start: string; end: string }
const DATE_KEY = /^\d{4}-\d{2}-\d{2}$/

export function localDayKey(date: Date): string {
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-')
}

export function parseTaskDateRange(search: string): TaskDateRange | null {
  const params = new URLSearchParams(search)
  const start = params.get('from') ?? ''
  const end = params.get('to') ?? ''
  if (!DATE_KEY.test(start) || !DATE_KEY.test(end) || start > end) return null
  if (!Number.isFinite(new Date(start + 'T12:00:00').getTime()) || !Number.isFinite(new Date(end + 'T12:00:00').getTime())) return null
  return { start, end }
}

export function taskCreatedOn(task: { createdAt: string }, range: TaskDateRange | null): boolean {
  if (!range) return true
  const date = new Date(task.createdAt)
  if (!Number.isFinite(date.getTime())) return false
  const day = localDayKey(date)
  return day >= range.start && day <= range.end
}

export function shiftDay(date: Date, days: number) {
  const next = new Date(date.getFullYear(), date.getMonth(), date.getDate() + days, 12)
  return localDayKey(next)
}
function readableDay(value: string) {
  const parts = value.split('-').map(Number)
  return `${parts[1]}月${parts[2]}日`
}
export function taskDateLabel(range: TaskDateRange | null, today = new Date()) {
  if (!range) return '全部时间'
  const now = localDayKey(today)
  if (range.start === now && range.end === now) return '今天'
  const yesterday = shiftDay(today, -1)
  if (range.start === yesterday && range.end === yesterday) return '昨天'
  if (range.start === shiftDay(today, -6) && range.end === now) return '近 7 天'
  if (range.start === shiftDay(today, -29) && range.end === now) return '近 30 天'
  return range.start === range.end ? readableDay(range.start) : `${readableDay(range.start)} — ${readableDay(range.end)}`
}

export function monthOffset(month: Date, offset: number) { return new Date(month.getFullYear(), month.getMonth() + offset, 1) }
export function monthDates(month: Date) {
  const start = (new Date(month.getFullYear(), month.getMonth(), 1).getDay() + 6) % 7
  const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()
  const rows = Math.ceil((start + days) / 7) * 7
  return Array.from({ length: rows }, (_, index) => new Date(month.getFullYear(), month.getMonth(), 1 - start + index, 12))
}
export function ordered(a: string, b: string): TaskDateRange { return a <= b ? { start: a, end: b } : { start: b, end: a } }
