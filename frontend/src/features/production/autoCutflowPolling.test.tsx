// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMultiCutflowTask, listMultiCutflowTasks, retryCutflowHandoff } from '../../api'
import type { MultiCutflowTask } from '../../api'
import type { Task } from '../../types'
import { useMultiCutBasket } from './useMultiCutBasket'

vi.mock('../../api', () => ({
  listCutflowHandoffs: vi.fn(async () => ({ enabled: true, handoffs: [] })),
  listMultiCutflowTasks: vi.fn(async () => ({ tasks: [] })),
  createMultiCutflowTask: vi.fn(), retryCutflowHandoff: vi.fn(),
}))
const readyAt = Date.parse('2026-11-13T06:23:36Z')
const task = {
  id: 'task-1', title: '自动送剪等待测试', phase: 'DELIVERABLE', assets: [],
  updatedAt: '2026-11-13T06:23:36Z', settings: { autoSubmitToCutflow: true },
  variants: [{ id: 'v1', downloadArtifactId: 'a1', generationStatus: 'SUCCEEDED', downloadStatus: 'READY', validationStatus: 'PASSED' }],
} as unknown as Task
let root: Root
function Harness({ tasks = [task] }: { tasks?: Task[] }) {
  const basket = useMultiCutBasket({ tasks, canWrite: true, enabled: true })
  return <><output>{basket.multiTaskError || String(basket.multiTasksCheckedAt)}</output>
    <p>{basket.submitHint}</p><button disabled={!basket.canSubmit} onClick={() => void basket.submit()}>送剪</button></>
}
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(readyAt); vi.clearAllMocks()
  vi.mocked(listMultiCutflowTasks).mockResolvedValue({ tasks: [] })
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  sessionStorage.clear(); document.body.innerHTML = '<div id="root"></div>'
  root = createRoot(document.getElementById('root')!)
})
afterEach(async () => {
  await act(async () => root.unmount())
  vi.useRealTimers(); vi.unstubAllGlobals()
  expect(createMultiCutflowTask).not.toHaveBeenCalled()
  expect(retryCutflowHandoff).not.toHaveBeenCalled()
})

describe('bounded read-only automatic CUT polling', () => {
  it('queries at five seconds, then returns to thirty seconds after three minutes', async () => {
    await act(async () => root.render(<Harness />))
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(1)
    await advance(180_000)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(37)
    await advance(29_999)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(37)
    await advance(1)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(38)
  })
  it('returns to normal active-job polling as soon as a record is found', async () => {
    await act(async () => root.render(<Harness />))
    vi.mocked(listMultiCutflowTasks).mockResolvedValue({ tasks: [{ id: 'cut', status: 'IMPORTING', sources: [{ taskId: task.id }], outputs: [] } as unknown as MultiCutflowTask] })
    await advance(5_000)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(2)
    await advance(14_999)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(2)
    await advance(1)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(3)
  })
  it('backs off query failures and resumes fast reads after recovery', async () => {
    vi.mocked(listMultiCutflowTasks).mockRejectedValueOnce(new Error('查询失败'))
    await act(async () => root.render(<Harness />))
    expect(document.querySelector('output')?.textContent).toBe('查询失败')
    await advance(29_999)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(2)
    await advance(5_000)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(3)
  })
  it('starts querying immediately when the final original becomes ready', async () => {
    const unfinished = { ...task, phase: 'GENERATING', variants: [{ ...task.variants[0], downloadArtifactId: null }] }
    await act(async () => root.render(<Harness tasks={[unfinished]} />))
    await advance(5_000)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(1)
    await act(async () => root.render(<Harness />))
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(2)
  })
  it('does not accelerate manual tasks and cancels pending reads on unmount', async () => {
    await act(async () => root.render(<Harness tasks={[{ ...task, settings: { autoSubmitToCutflow: false } }]} />))
    await advance(5_000)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(1)
    await act(async () => root.render(null))
    await advance(60_000)
    expect(listMultiCutflowTasks).toHaveBeenCalledTimes(1)
  })
  it('blocks saved basket selections while automatic admission is still pending', async () => {
    sessionStorage.setItem('cutflow-multi-source-selection', JSON.stringify(['v1']))
    await act(async () => root.render(<Harness />))
    expect(document.querySelector('button')?.disabled).toBe(true)
    expect(document.querySelector('p')?.textContent).toContain('等待自动送剪')
    await act(async () => document.querySelector('button')!.click())
    await advance(180_000)
    expect(document.querySelector('button')?.disabled).toBe(true)
  })
  it('preserves explicit manual recovery after a recorded CUT failure', async () => {
    sessionStorage.setItem('cutflow-multi-source-selection', JSON.stringify(['v1']))
    vi.mocked(listMultiCutflowTasks).mockResolvedValue({ tasks: [{
      id: 'cut-failed', status: 'FAILED', sources: [{ taskId: task.id, variantId: 'v1' }], outputs: [],
    } as unknown as MultiCutflowTask] })
    await act(async () => root.render(<Harness />))
    expect(document.querySelector('p')?.textContent).not.toContain('等待创建送剪任务')
    expect(document.querySelector('button')?.disabled).toBe(false)
  })
})
