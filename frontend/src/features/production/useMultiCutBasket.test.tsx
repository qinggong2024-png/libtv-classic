import { teardownReliabilityDom } from '../../test-helpers/reliabilityDom.mjs'
import { act, useEffect } from 'react'
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMultiCutflowTask, listCutflowHandoffs, listMultiCutflowTasks } from '../../api'
import type { Task } from '../../types'
import { mount } from '../../test-helpers/reactHarness'
import { useMultiCutBasket } from './useMultiCutBasket'

vi.mock('../../api', async original => ({
  ...await original<typeof import('../../api')>(),
  createMultiCutflowTask: vi.fn(),
  listCutflowHandoffs: vi.fn(),
  listMultiCutflowTasks: vi.fn(),
  retryCutflowHandoff: vi.fn(),
}))

const sample = {
  id: 'task-a',
  title: '测试原片',
  siteProfileId: 'site-1',
  updatedAt: '2026-11-12T15:00:00Z',
  variants: [{ id: 'v1', kind: 'STABLE', downloadArtifactId: 'artifact-1' }],
} as unknown as Task
let state: ReturnType<typeof useMultiCutBasket>
let view: Awaited<ReturnType<typeof mount>>

function Harness() {
  const controller = useMultiCutBasket({ tasks: [sample], enabled: true, canWrite: true,
    storageKey: 'test-single-direct-cut' })
  useEffect(() => { state = controller })
  return null
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.mocked(listCutflowHandoffs).mockResolvedValue({ enabled: true, handoffs: [] })
  vi.mocked(listMultiCutflowTasks).mockResolvedValue({ tasks: [] })
  vi.mocked(createMultiCutflowTask).mockResolvedValue({} as never)
  sessionStorage.removeItem('test-single-direct-cut')
})
afterEach(async () => {
  await view?.unmount()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  sessionStorage.removeItem('test-single-direct-cut')
})
afterAll(teardownReliabilityDom)

describe('单条原片直接送剪', () => {
  it('重新查询真实剪辑状态后，按原有单源合同提交一次', async () => {
    view = await mount(<Harness />)
    await act(async () => { await state.submitOne('v1') })
    expect(listCutflowHandoffs).toHaveBeenCalledWith('v1')
    expect(listMultiCutflowTasks).toHaveBeenCalled()
    expect(createMultiCutflowTask).toHaveBeenCalledOnce()
    expect(createMultiCutflowTask).toHaveBeenCalledWith(['v1'], expect.stringMatching(/^cutflow-job-/), true, true)
    expect(state.selection).toHaveLength(0)
  })

  it('已在剪辑的原片不允许直接重复提交', async () => {
    vi.mocked(listMultiCutflowTasks).mockResolvedValue({
      tasks: [{ id: 'editing', status: 'EDITING', sources: [{ variantId: 'v1' }], outputs: [] }],
    } as never)
    view = await mount(<Harness />)
    await act(async () => { await state.submitOne('v1') })
    expect(createMultiCutflowTask).not.toHaveBeenCalled()
    expect(state.message).toContain('已有进行中的剪辑')
  })

  it('已有跨任务选择时，不会直接送剪或清空选片篮', async () => {
    view = await mount(<Harness />)
    await act(async () => { state.add('v1') })
    await act(async () => { await state.submitOne('v1') })
    expect(createMultiCutflowTask).not.toHaveBeenCalled()
    expect(state.selection).toEqual(['v1'])
  })
})
