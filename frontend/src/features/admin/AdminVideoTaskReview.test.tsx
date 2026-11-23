import { act } from 'react'
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  fetchAdminCutObjectUrl, fetchAdminVideoObjectUrl,
  getAdminVideoTasks, getAdminVideoTrace,
} from '../../api'
import type { AdminVideoRecord, AdminVideoTaskPage } from '../../types'
import { mount } from '../../test-helpers/reactHarness'
import { teardownReliabilityDom } from '../../test-helpers/reliabilityDom.mjs'
import { AdminVideoTaskReview } from './AdminVideoTaskReview'

vi.mock('../../api', async original => ({
  ...await original<typeof import('../../api')>(),
  getAdminVideoTasks: vi.fn(),
  getAdminVideoTrace: vi.fn(),
  fetchAdminCutObjectUrl: vi.fn(),
  fetchAdminVideoObjectUrl: vi.fn(),
}))
const raw = (id: string, index: number): AdminVideoRecord => ({
  artifactId: id, attemptId: `attempt-${id}`, taskId: 'task-a',
  title: '两条原片合成', username: 'pw-account', brandId: null,
  brandName: 'Pagewings', createdAt: '2026-11-23T04:00:00Z',
  durationSeconds: 10, width: 1080, height: 1920,
  variant: 'STABLE', variantId: `variant-${id}`, variantIndex: index,
  provider: 'kling', validationStatus: 'PASSED', finalApprovalStatus: 'PENDING',
})
const data: AdminVideoTaskPage = {
  total: 1, limit: 24, offset: 0, days: 7,
  healthSummary: { critical: 0, warning: 0, normal: 1 },
  items: [{
    taskId: 'task-a', title: '两条原片合成', username: 'pw-account',
    brandName: 'Pagewings', createdAt: '2026-11-23T04:00:00Z',
    clips: [raw('clip-1', 1), raw('clip-2', 2)],
    rawClipCount: 2, severity: 'normal',
    cut: { kind: 'multi', batchId: 'batch-1', outputId: 'final',
      durationSeconds: 19.5, createdAt: '2026-11-23T04:10:00Z', status: 'COMPLETED' },
    cutStatus: 'COMPLETED',
  }],
}
let view: Awaited<ReturnType<typeof mount>>
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.mocked(getAdminVideoTasks).mockResolvedValue(data)
  vi.mocked(getAdminVideoTrace).mockRejectedValue(new Error('unavailable'))
  vi.mocked(fetchAdminCutObjectUrl).mockRejectedValue(new Error('test video unavailable'))
  vi.mocked(fetchAdminVideoObjectUrl).mockRejectedValue(new Error('test video unavailable'))
})
afterEach(async () => {
  await view?.unmount()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
})
afterAll(teardownReliabilityDom)

it('shows one task card for two raw clips and launches the real CUT playback', async () => {
  view = await mount(<AdminVideoTaskReview adminId="admin-id"/>)
  await act(async () => { await Promise.resolve() })
  expect(view.host.querySelectorAll('.admin-task-card')).toHaveLength(1)
  expect(view.host.querySelector('.admin-task-card img')?.getAttribute('src'))
    .toContain('/cut/multi/batch-1/final/thumbnail')
  expect(view.host.textContent).toContain('2 条原片')
  const button = view.host.querySelector<HTMLButtonElement>('button[aria-label^="播放成片"]')
  expect(button).not.toBeNull()
  await act(async () => { button?.click(); await Promise.resolve() })
  expect(fetchAdminCutObjectUrl).toHaveBeenCalledWith('admin-id', 'task-a', data.items[0].cut)
  expect(fetchAdminVideoObjectUrl).not.toHaveBeenCalled()
  expect(view.host.textContent).not.toContain('人工验收')
})

it('falls back to correctly labelled raw playback without a CUT output', async () => {
  vi.mocked(getAdminVideoTasks).mockResolvedValue({
    ...data, items: [{ ...data.items[0], cut: null, cutStatus: 'QUEUED' }],
  })
  view = await mount(<AdminVideoTaskReview adminId="admin-id"/>)
  await act(async () => { await Promise.resolve() })
  expect(view.host.querySelector('.admin-task-card img')?.getAttribute('src'))
    .toContain('clip-1/thumbnail')
  const button = view.host.querySelector<HTMLButtonElement>('button[aria-label^="播放原片"]')
  expect(button).not.toBeNull()
  await act(async () => { button?.click(); await Promise.resolve() })
  expect(fetchAdminVideoObjectUrl).toHaveBeenCalledWith('admin-id', 'clip-1')
  expect(fetchAdminCutObjectUrl).not.toHaveBeenCalled()
})

it('opens a non-blocking detail with both raw clips, two prompt comparisons and timings', async () => {
  view = await mount(<AdminVideoTaskReview adminId="admin-id"/>)
  await act(async () => { await Promise.resolve() })
  const button = [...view.host.querySelectorAll('button')].find(el => el.textContent?.includes('查看完整详情'))
  await act(async () => { button?.click(); await Promise.resolve() })
  expect(view.host.querySelectorAll('.admin-task-video')).toHaveLength(3)
  expect(view.host.querySelectorAll('.admin-task-prompt-block')).toHaveLength(2)
  expect(view.host.textContent).toContain('Sol 编排与实际提交 Kling 的提示词')
  expect(view.host.textContent).toContain('各环节生产耗时')
  expect(view.host.textContent).toContain('返回视频目录')
  expect(view.host.textContent).not.toContain('审核通过')
})
