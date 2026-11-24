import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

const date = (day: string) => new Date(day + 'T00:00:00Z')
const iso = (day: Date) => day.toISOString().slice(0, 10)
const monthStart = (day: string) => day.slice(0, 7) + '-01'
const ordered = (a: string, b: string): [string, string] => a <= b ? [a, b] : [b, a]

export function UsageDateRangePicker({ start, end, today, onChange }: { start: string; end: string; today: string; onChange: (start: string, end: string) => void }) {
  const [open, setOpen] = useState(true)
  const [month, setMonth] = useState(() => monthStart(end))
  const [anchor, setAnchor] = useState<string | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const wrapper = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const drag = useRef<{ start: string; last: string } | null>(null)
  const suppressClick = useRef(false)
  const close = () => { setOpen(false); setAnchor(null); setHover(null); drag.current = null }
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) {
        setOpen(false); setAnchor(null); setHover(null); drag.current = null
      }
    }
    const release = () => { drag.current = null }
    document.addEventListener('pointerdown', outside)
    window.addEventListener('mouseup', release)
    return () => { document.removeEventListener('pointerdown', outside); window.removeEventListener('mouseup', release) }
  }, [open])
  const moveMonth = (delta: number) => {
    const value = date(month)
    value.setUTCMonth(value.getUTCMonth() + delta)
    setMonth(iso(value))
  }
  const choose = (day: string) => {
    if (suppressClick.current) { suppressClick.current = false; return }
    if (anchor) { onChange(...ordered(anchor, day)); close(); trigger.current?.focus() }
    else { onChange(day, day); setAnchor(day); setHover(day) }
  }
  const first = date(month)
  first.setUTCDate(1 - (first.getUTCDay() + 6) % 7)
  const days = Array.from({ length: 42 }, (_, i) => {
    const day = new Date(first)
    day.setUTCDate(day.getUTCDate() + i)
    return iso(day)
  })
  const [previewStart, previewEnd] = anchor && hover ? ordered(anchor, hover) : [start, end]
  return <div className="admin-usage-dates" ref={wrapper} onKeyDown={(event) => { if (event.key === 'Escape') { close(); trigger.current?.focus() } }}>
    <button ref={trigger} type="button" className="secondary-button usage-range-trigger" aria-expanded={open} aria-label="选择统计日期范围" onClick={() => open ? close() : (setMonth(monthStart(end)), setOpen(true))}><CalendarDays size={16} />{start === end ? start : start + ' 至 ' + end}</button>
    {open && <div className="usage-range-calendar" role="group" aria-label="日期范围日历">
      <div className="usage-range-month"><button type="button" aria-label="上个月" onClick={() => moveMonth(-1)}><ChevronLeft size={18} /></button><strong>{month.slice(0, 4)}年{Number(month.slice(5, 7))}月</strong><button type="button" aria-label="下个月" disabled={month >= monthStart(today)} onClick={() => moveMonth(1)}><ChevronRight size={18} /></button></div>
      <div className="usage-range-grid">{['一', '二', '三', '四', '五', '六', '日'].map(day => <span key={day}>{day}</span>)}
        {days.map(day => <button key={day} type="button" aria-label={day} aria-pressed={day >= start && day <= end} aria-current={day === today ? 'date' : undefined} disabled={day > today}
          className={[day.slice(0, 7) !== month.slice(0, 7) ? 'outside-month' : '', day >= previewStart && day <= previewEnd ? 'in-range' : '', day === previewStart || day === previewEnd ? 'range-edge' : ''].join(' ')}
          onMouseDown={(event) => { if (event.button !== 0) return; suppressClick.current = false; drag.current = { start: day, last: day } }}
          onMouseEnter={() => { if (drag.current) { drag.current.last = day; setAnchor(drag.current.start); setHover(day) } else if (anchor) setHover(day) }}
          onMouseUp={() => { const active = drag.current; drag.current = null; if (active && active.start !== day) { suppressClick.current = true; onChange(...ordered(active.start, day)); close(); trigger.current?.focus() } }}
          onClick={(event) => { if (event.detail === 0) suppressClick.current = false; choose(day) }}>{Number(day.slice(8))}</button>)}
      </div>
      <div className="usage-range-bottom"><span>{anchor ? '再选一天扩展范围' : '点选一天，或拖动选择区间'}</span><button type="button" className="secondary-button" onClick={() => { close(); trigger.current?.focus() }}>完成</button></div>
    </div>}
  </div>
}
