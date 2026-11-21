import { teardownReliabilityDom } from '../../../test-helpers/reliabilityDom.mjs'
import { act, useEffect } from 'react'
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getCurrentUser, getSystemStatus, getTask, getTaskPage } from '../../../api'
import { taskNotificationForTransition } from '../../../browserNotifications'
import type { AuthUser, SystemStatus, Task } from '../../../types'
import { deferred, mount } from '../../../test-helpers/reactHarness'
import { useTaskPolling } from './useTaskPolling'

vi.mock('../../../api', async (original) => ({
  ...await original<typeof import('../../../api')>(),
  getCurrentUser: vi.fn(), getSystemStatus: vi.fn(), getTask: vi.fn(), getTaskPage: vi.fn(),
  setApiUserId: vi.fn(), setCsrfCookieName: vi.fn(),
}))
const task = (id: string, phase = 'GENERATING') => ({ id, phase, title: id } as Task)
let view: Awaited<ReturnType<typeof mount>>
let state: ReturnType<typeof useTaskPolling>
function Harness({ detailId }: { detailId: string | null }) {
  const current = useTaskPolling(detailId)
  useEffect(() => { state = current })
  return null
}
afterAll(teardownReliabilityDom)
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  vi.mocked(getCurrentUser).mockImplementation(async () => ({ userId: 'synthetic-user', role: 'USER' } as AuthUser))
  vi.mocked(getSystemStatus).mockResolvedValue({ sharedAuthentication: true } as SystemStatus)
  vi.mocked(getTaskPage).mockImplementation(async () => ({ items: [task('selected'), task('other')], nextCursor: null }))
  vi.mocked(getTask).mockImplementation(async (id) => task(id))
})
afterEach(async () => {
  await view?.unmount()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
})
describe('independent task polling clocks', () => {
  it('keeps the 30s list fallback and other-task notifications through 4s detail updates', async () => {
    view = await mount(<Harness detailId="selected" />)
    await advance(0)
    for (let i = 0; i < 7; i++) await advance(4000)
    expect(getTask).toHaveBeenCalledTimes(7)
    expect(getTaskPage).toHaveBeenCalledTimes(1)
    vi.mocked(getTaskPage).mockResolvedValue({ items: [task('selected'), task('other', 'DELIVERABLE')], nextCursor: null })
    await advance(2000)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
    expect(taskNotificationForTransition('GENERATING', state.tasks.find((item) => item.id === 'other')!)?.tag).toBe('task:other:completed')
    for (let i = 0; i < 7; i++) await advance(4000)
    await advance(2000)
    expect(getTaskPage).toHaveBeenCalledTimes(3)
  })
  it('does not restart either clock on unrelated updates or detail switches', async () => {
    view = await mount(<Harness detailId="selected" />)
    await advance(0)
    await advance(3000)
    await act(async () => { state.setTasks((items) => items.map((item) => ({ ...item }))) })
    await advance(1000)
    expect(getTask).toHaveBeenCalledTimes(1)
    await advance(25000)
    await view.render(<Harness detailId="other" />)
    await advance(1000)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
  })
  it('uses 10s for active lists and 30s when idle', async () => {
    view = await mount(<Harness detailId={null} />)
    await advance(0)
    await advance(9000)
    await act(async () => { state.setTasks((items) => [...items]) })
    await advance(1000)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
    await act(async () => { state.setTasks([task('selected', 'DELIVERABLE')]) })
    await advance(29999)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
    await advance(1)
    expect(getTaskPage).toHaveBeenCalledTimes(3)
  })
  it('coalesces manual calls with an in-flight automatic list request', async () => {
    view = await mount(<Harness detailId={null} />)
    await advance(0)
    const late = deferred<Awaited<ReturnType<typeof getTaskPage>>>()
    vi.mocked(getTaskPage).mockReturnValueOnce(late.promise)
    await advance(10000)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
    let first!: Promise<void>
    let second!: Promise<void>
    await act(async () => {
      first = state.refreshTasks()
      second = state.refreshTasks()
      await Promise.resolve()
    })
    expect(first).toBe(second)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
    await act(async () => {
      late.resolve({ items: [task('selected'), task('other')], nextCursor: null })
      await Promise.all([first, second])
    })
    await act(async () => { await state.refreshTasks() })
    expect(getTaskPage).toHaveBeenCalledTimes(3)
  })
  it('defers the next background list poll after a manual refresh', async () => {
    view = await mount(<Harness detailId={null} />)
    await advance(0)
    await advance(7000)
    await act(async () => { await state.refreshTasks() })
    expect(getTaskPage).toHaveBeenCalledTimes(2)
    await advance(3000)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
    await advance(6999)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
    await advance(1)
    expect(getTaskPage).toHaveBeenCalledTimes(3)
  })
  it.each(['offline', 'hidden'])('pauses while %s and resumes', async (mode) => {
    view = await mount(<Harness detailId="selected" />)
    await advance(0)
    if (mode === 'offline') vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    else vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    await advance(30000)
    expect(getTask).not.toHaveBeenCalled()
    expect(getTaskPage).toHaveBeenCalledTimes(1)
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    await advance(2000)
    expect(getTask).toHaveBeenCalledTimes(1)
    await advance(28000)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
  })
  it('does not overlap slow detail calls or apply a response after switching detail', async () => {
    const late = deferred<Task>()
    vi.mocked(getTask).mockReturnValueOnce(late.promise)
    view = await mount(<Harness detailId="selected" />)
    await advance(0)
    await advance(4000)
    await act(async () => { state.setTasks((items) => [...items]) })
    await advance(12000)
    expect(getTask).toHaveBeenCalledTimes(1)
    await view.render(<Harness detailId="other" />)
    await act(async () => { late.resolve(task('selected', 'DELIVERABLE')) })
    expect(state.tasks.find((item) => item.id === 'selected')?.phase).toBe('GENERATING')
    await advance(4000)
    expect(getTask).toHaveBeenLastCalledWith('other')
  })
  it('recovers detail failures, stops completed detail and preserves list fallback', async () => {
    vi.mocked(getTask).mockRejectedValueOnce(new Error('injected fault')).mockResolvedValue(task('selected', 'DELIVERABLE'))
    view = await mount(<Harness detailId="selected" />)
    await advance(0)
    await advance(4000)
    await advance(4000)
    expect(state.tasks.find((item) => item.id === 'selected')?.phase).toBe('DELIVERABLE')
    await advance(21999)
    expect(getTask).toHaveBeenCalledTimes(2)
    expect(getTaskPage).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(getTaskPage).toHaveBeenCalledTimes(2)
  })
  it('cancels clocks on unmount, including slow completion', async () => {
    const late = deferred<Task>()
    vi.mocked(getTask).mockReturnValue(late.promise)
    view = await mount(<Harness detailId="selected" />)
    await advance(0)
    await advance(4000)
    await view.render(null)
    await act(async () => { late.resolve(task('selected')) })
    await advance(60000)
    expect(getTask).toHaveBeenCalledTimes(1)
    expect(getTaskPage).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })
})
