// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Task } from '../../types'
import { useMultiCutBasket } from './useMultiCutBasket'

const api = vi.hoisted(() => ({
  reads: 0,
  resolveRefresh: null as null | ((value: { tasks: [] }) => void),
  listMultiCutflowTasks: vi.fn(),
  listCutflowHandoffs: vi.fn(),
}))
vi.mock('../../api', () => ({
  listMultiCutflowTasks: api.listMultiCutflowTasks,
  listCutflowHandoffs: api.listCutflowHandoffs,
  createMultiCutflowTask: vi.fn(),
  retryCutflowHandoff: vi.fn(),
}))

const testTasks = [{
  id: 'task-stable-cut-status',
  title: '有原片的任务',
  phase: 'DELIVERABLE',
  variants: [{ id: 'variant-a', downloadArtifactId: 'artifact-a' }],
  settings: { autoSubmitToCutflow: false },
}] as unknown as Task[]

function Harness() {
  const basket = useMultiCutBasket({ tasks: testTasks, canWrite: true, enabled: true, storageKey: 'basket-status-stability-test' })
  return <div>
    <output data-testid="loaded">{String(basket.multiTasksLoaded)}</output>
    <output data-testid="can-submit">{String(basket.canSubmit)}</output>
    <button type="button" onClick={() => basket.toggle('variant-a')}>选片</button>
  </div>
}

afterEach(() => {
  api.listMultiCutflowTasks.mockReset()
  api.listCutflowHandoffs.mockReset()
  api.resolveRefresh = null
  api.reads = 0
  sessionStorage.removeItem('basket-status-stability-test')
})

describe('CUT 状态在选片期间稳定', () => {
  it('已有 CUT 快照时不会因选择原片闪回未加载，同时等待新核验时禁止送剪', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    api.listMultiCutflowTasks.mockImplementation(() => {
      api.reads += 1
      if (api.reads === 1) return Promise.resolve({ tasks: [] })
      return new Promise(resolve => { api.resolveRefresh = resolve })
    })
    api.listCutflowHandoffs.mockResolvedValue({ enabled: true, handoffs: [] })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    try {
      await act(async () => root.render(<Harness />))
      expect(host.querySelector('[data-testid="loaded"]')?.textContent).toBe('true')
      await act(async () => host.querySelector<HTMLButtonElement>('button')?.click())
      expect(api.listMultiCutflowTasks).toHaveBeenCalledTimes(2)
      expect(host.querySelector('[data-testid="loaded"]')?.textContent).toBe('true')
      expect(host.querySelector('[data-testid="can-submit"]')?.textContent).toBe('false')
      await act(async () => { api.resolveRefresh?.({ tasks: [] }) })
      expect(host.querySelector('[data-testid="loaded"]')?.textContent).toBe('true')
    } finally {
      await act(async () => root.unmount())
      host.remove()
      vi.unstubAllGlobals()
    }
  })
})
