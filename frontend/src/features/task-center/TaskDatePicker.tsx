import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight, X } from 'lucide-react'
import { useEffect, useRef, useState, type PointerEvent } from 'react'

import { localDayKey, monthDates, monthOffset, ordered, shiftDay, taskDateLabel, type TaskDateRange } from './TaskDateRange'

export function TaskDatePicker({ value, onChange }: { value: TaskDateRange | null; onChange: (value: TaskDateRange | null) => void }) {
  const [open, setOpen] = useState(false)
  const [rightMonth, setRightMonth] = useState(() => monthOffset(new Date(), 0))
  const [anchor, setAnchor] = useState<string | null>(null)
  const [draft, setDraft] = useState<TaskDateRange | null>(null)
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef<{ pointerId: number; from: string; to: string; moved: boolean } | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const today = new Date()
  const todayKey = localDayKey(today)
  const picked = draft ?? value
  const presets = [
    { name: '全部时间', value: null },
    { name: '今天', value: ordered(todayKey, todayKey) },
    { name: '昨天', value: ordered(shiftDay(today, -1), shiftDay(today, -1)) },
    { name: '近 7 天', value: ordered(shiftDay(today, -6), todayKey) },
    { name: '近 30 天', value: ordered(shiftDay(today, -29), todayKey) },
  ]

  useEffect(() => {
    if (!open) return
    const outside = (event: globalThis.PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false); setAnchor(null); setDraft(null)
      }
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); setAnchor(null); setDraft(null) }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) }
  }, [open])

  const pickDay = (day: string) => {
    if (anchor) {
      onChange(ordered(anchor, day))
      setAnchor(null)
    } else {
      onChange(ordered(day, day))
      setAnchor(day)
    }
    setDraft(null)
  }
  const pointerTargetDay = (event: PointerEvent<HTMLDivElement>) => {
    const target = document.elementFromPoint(event.clientX, event.clientY) as HTMLElement | null
    const button = target?.closest<HTMLButtonElement>('button[data-task-day]')
    return button && !button.disabled ? button.dataset.taskDay ?? null : null
  }
  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-task-day]')
    if (event.button !== 0 || !button || button.disabled || !button.dataset.taskDay) return
    event.preventDefault()
    dragRef.current = { pointerId: event.pointerId, from: button.dataset.taskDay, to: button.dataset.taskDay, moved: false }
    event.currentTarget.setPointerCapture(event.pointerId)
    setDragging(true)
    setDraft(ordered(button.dataset.taskDay, button.dataset.taskDay))
  }
  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const day = pointerTargetDay(event)
    if (day) {
      drag.to = day
      if (day !== drag.from) drag.moved = true
      setDraft(ordered(drag.from, day))
    }
  }
  const handlePointerUp = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    dragRef.current = null
    const to = pointerTargetDay(event) ?? drag.to
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    setDragging(false)
    setDraft(null)
    if (drag.moved) { setAnchor(null); onChange(ordered(drag.from, to)) }
    else pickDay(drag.from)
  }
  const months = [monthOffset(rightMonth, -1), rightMonth]
  return <div ref={rootRef} className="task-date-picker">
    <button type="button" className={'task-date-trigger' + (value ? ' is-selected' : '')} aria-expanded={open} aria-haspopup="dialog" onClick={() => {
      if (!open && value) {
        const [year, month] = value.end.split('-').map(Number)
        setRightMonth(new Date(year, month - 1, 1))
      }
      setAnchor(null); setDraft(null); setOpen(!open)
    }}><CalendarDays size={17} />{taskDateLabel(value)}<ChevronDown size={15} /></button>
    {open ? <section className="task-date-popover" role="dialog" aria-label="选择任务创建时间">
      <div className="task-date-header"><strong>选择任务创建时间</strong><button type="button" aria-label="关闭日期选择" onClick={() => setOpen(false)}><X size={17} /></button></div>
      <div className="task-date-body">
        <div className="task-date-presets">
          {presets.map(preset => <button key={preset.name} type="button" className={taskDateLabel(value, today) === preset.name ? 'is-selected' : ''} onClick={() => {
            onChange(preset.value); setAnchor(null); setDraft(null); setOpen(false)
          }}>{preset.name}</button>)}
        </div>
        <div className="task-date-calendar">
          <div className="task-date-month-nav"><button type="button" aria-label="上一个月" onClick={() => setRightMonth(current => monthOffset(current, -1))}><ChevronLeft size={19} /></button>
            <button type="button" aria-label="下一个月" disabled={monthOffset(rightMonth, 1).getTime() > today.getTime()} onClick={() => setRightMonth(current => monthOffset(current, 1))}><ChevronRight size={19} /></button>
          </div>
          <div className={'task-date-months' + (dragging ? ' is-dragging' : '')} onPointerDown={handlePointerDown} onPointerMove={handlePointerMove} onPointerUp={handlePointerUp} onPointerCancel={() => { dragRef.current = null; setDragging(false); setDraft(null) }}>
            {months.map(month => <div className="task-date-month" key={month.toISOString()}>
              <h4>{month.getFullYear()}年{month.getMonth() + 1}月</h4>
              <div className="task-date-grid task-date-weekdays">{['一', '二', '三', '四', '五', '六', '日'].map(day => <span key={day}>{day}</span>)}</div>
              <div className="task-date-grid">
                {monthDates(month).map(day => {
                  const key = localDayKey(day)
                  const inRange = picked && key >= picked.start && key <= picked.end
                  return <button key={key} type="button" data-task-day={key} aria-label={key} aria-pressed={Boolean(inRange)} disabled={key > todayKey} className={'task-date-day' + (day.getMonth() !== month.getMonth() ? ' is-other' : '') + (inRange ? ' in-range' : '') + (key === picked?.start ? ' is-start' : '') + (key === picked?.end ? ' is-end' : '') + (key === todayKey ? ' is-today' : '')} onClick={event => { if (event.detail === 0) pickDay(key) }}>{day.getDate()}</button>
                })}
              </div>
            </div>)}
          </div>
        </div>
      </div>
      <div className="task-date-footer"><span>当前范围：<strong>{taskDateLabel(picked)}</strong></span><div><button type="button" onClick={() => {onChange(null);setAnchor(null);setDraft(null)}}>清除日期</button><button type="button" className="task-date-done" onClick={() => {setOpen(false);setAnchor(null);setDraft(null)}}>完成</button></div></div>
    </section> : null}
  </div>
}
