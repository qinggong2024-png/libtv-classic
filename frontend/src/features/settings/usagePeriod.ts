export type UsagePeriod = 'today' | 'week' | 'month' | 'custom'

export function shanghaiToday() {
  const parts = new Intl.DateTimeFormat('en', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date())
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((entry) => entry.type === type)!.value
  return `${part('year')}-${part('month')}-${part('day')}`
}

export function usageDateRange(period: UsagePeriod, today: string, start: string, end: string) {
  if (period === 'custom') return { startDate: start, endDate: end }
  if (period === 'month') return { startDate: `${today.slice(0, 7)}-01`, endDate: today }
  if (period === 'week') {
    const monday = new Date(`${today}T00:00:00Z`)
    monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() + 6) % 7)
    return { startDate: monday.toISOString().slice(0, 10), endDate: today }
  }
  return { startDate: today, endDate: today }
}
