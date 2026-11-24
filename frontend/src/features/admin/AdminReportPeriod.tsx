import type { UsagePeriod } from '../settings/usagePeriod'
import { UsageDateRangePicker } from './UsageDateRangePicker'

const periods: { value: UsagePeriod; label: string }[] = [
  { value: 'today', label: '今天' }, { value: 'week', label: '本周' },
  { value: 'month', label: '本月' }, { value: 'custom', label: '自定义' },
]

export function AdminReportPeriod({ period, today, start, end, onPeriod, onRange }: {
  period: UsagePeriod; today: string; start: string; end: string
  onPeriod: (period: UsagePeriod) => void; onRange: (start: string, end: string) => void
}) {
  return <>
    <div className="admin-usage-periods" data-period={period} role="group" aria-label="统计范围">{periods.map(item => <button type="button" key={item.value} aria-pressed={period === item.value} onClick={() => onPeriod(item.value)}>{item.label}</button>)}</div>
    {period === 'custom' && <UsageDateRangePicker start={start} end={end} today={today} onChange={onRange} />}
  </>
}
