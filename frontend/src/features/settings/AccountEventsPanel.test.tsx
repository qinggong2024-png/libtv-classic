// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getAccountEvents } from '../../api'
import { AccountEventsPanel } from './AccountEventsPanel'
import type { AccountEventReport } from '../../types'

vi.mock('../../api', () => ({ getAccountEvents: vi.fn() }))
const event = { id: '1', username: 'GC', action: 'LOGIN', createdAt: '2026-11-24T04:23:01Z', entityType: 'user', entityId: 'user-1' }
const report: AccountEventReport = { view: 'production', startDate: '2026-11-23', endDate: '2026-11-24', total: 45, items: [event] }
let root: Root
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.setSystemTime(new Date('2026-11-24T04:30:00Z'))
  vi.mocked(getAccountEvents).mockReset().mockResolvedValue(report)
  document.body.innerHTML = '<div id="test-root"></div>'
  root = createRoot(document.getElementById('test-root')!)
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false }
})
afterEach(async () => { await act(async () => root.unmount()); vi.useRealTimers(); vi.unstubAllGlobals() })
const render = async (actorId = 'root') => { await act(async () => root.render(<AccountEventsPanel actorId={actorId} users={[]} brands={[]} />)) }
const click = async (label: string) => { await act(async () => Array.from(document.querySelectorAll('button')).find(button => button.textContent === label || button.getAttribute('aria-label') === label)!.click()) }

describe('shared admin audit report design', () => {
  it('uses the shared period control, a single date range, and correct Shanghai time', async () => {
    await render()
    expect(document.querySelector('.admin-usage-periods[data-period="week"]')).not.toBeNull()
    expect(document.querySelectorAll('input[type=date]')).toHaveLength(0)
    expect(document.querySelector('tbody time')?.textContent).toContain('12:23:01')
    await click('自定义')
    await click('2026-11-18')
    await click('2026-11-20')
    expect(getAccountEvents).toHaveBeenLastCalledWith(expect.objectContaining({ startDate: '2026-11-18', endDate: '2026-11-20', offset: 0 }), 'root')
  })
  it('opens detail in a drawer without changing the table, and clears on query change', async () => {
    vi.mocked(getAccountEvents).mockResolvedValue({ ...report, items: [event, { ...event, id: '2', action: 'CREATE_VIDEO_TASK', quantity: 2, detailTitle: '测试视频', entityId: 'task-2' }] })
    await render()
    expect(document.querySelectorAll('button[aria-label="查看操作详情"]')).toHaveLength(1)
    expect(document.querySelector('tbody')?.textContent).toContain('创建生视频任务 2 条')
    await click('查看操作详情')
    expect(document.querySelector('dialog[open]')?.textContent).toContain('task-2')
    expect(document.querySelector('tbody')?.textContent).not.toContain('user-1')
    await render('another-actor')
    expect(document.querySelector('dialog')).toBeNull()
  })
  it('hides the whole details column when nothing extra is available and displays counts only from data', async () => {
    vi.mocked(getAccountEvents).mockResolvedValue({ ...report, items: [event, { ...event, id: '2', action: 'CREATE_IMAGE_TASK', quantity: 2 }, { ...event, id: '3', action: 'CREATE_VIDEO_TASK' }] })
    await render()
    expect(Array.from(document.querySelectorAll('thead th')).map(cell => cell.textContent)).toEqual(['时间','操作账号','操作内容'])
    expect(document.querySelector('tbody')?.textContent).toContain('创建生图任务 2 张')
    expect(document.querySelector('tbody tr:last-child td:last-child')?.textContent).toBe('创建生视频任务')
    expect(document.querySelector('button[aria-label="查看操作详情"]')).toBeNull()
  })
  it('does not silently display incomplete audit-only results as a production report', async () => {
    vi.mocked(getAccountEvents).mockResolvedValue({ ...report, view: undefined })
    await render()
    expect(document.querySelector('[role=alert]')?.textContent).toContain('尚未启用')
    expect(document.querySelector('table')).toBeNull()
  })
  it('preserves pagination, filters and retries without showing stale records', async () => {
    await render()
    await click('操作记录下一页')
    expect(getAccountEvents).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 20 }), 'root')
    const select = document.querySelector<HTMLSelectElement>('select[aria-label="操作类型"]')!
    await act(async () => { select.value = 'SYSTEM'; select.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(getAccountEvents).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, category: 'SYSTEM', view: 'production' }), 'root')
    vi.mocked(getAccountEvents).mockRejectedValueOnce(new Error('read failed'))
    await click('刷新记录')
    expect(document.querySelector('[role=alert]')).not.toBeNull()
    expect(document.querySelector('tbody')).toBeNull()
    await click('刷新记录')
    expect(document.querySelector('tbody')).not.toBeNull()
  })
})
