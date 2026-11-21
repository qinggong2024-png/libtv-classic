import { afterEach, describe, expect, it, vi } from 'vitest'
import { shanghaiToday, usageDateRange } from './features/settings/usagePeriod'

afterEach(() => vi.useRealTimers())

describe('usage reporting business dates', () => {
  it('uses Shanghai midnight rather than the machine timezone', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-18T16:00:00Z'))
    expect(shanghaiToday()).toBe('2026-10-19')
  })
  it('starts the week on Monday across month and year boundaries', () => {
    expect(usageDateRange('week', '2027-01-03', '', '')).toEqual({ startDate: '2026-12-28', endDate: '2027-01-03' })
    expect(usageDateRange('week', '2026-10-19', '', '')).toEqual({ startDate: '2026-10-19', endDate: '2026-10-19' })
  })
  it('uses calendar month to date and preserves custom endpoints', () => {
    expect(usageDateRange('month', '2026-10-19', '', '')).toEqual({ startDate: '2026-10-01', endDate: '2026-10-19' })
    expect(usageDateRange('custom', '2026-10-19', '2026-09-15', '2026-10-15')).toEqual({ startDate: '2026-09-15', endDate: '2026-10-15' })
  })
})
