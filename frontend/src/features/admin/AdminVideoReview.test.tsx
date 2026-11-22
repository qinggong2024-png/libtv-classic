import { act } from 'react'
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getAdminVideos } from '../../api'
import type { AdminVideoPage } from '../../types'
import { mount } from '../../test-helpers/reactHarness'
import { teardownReliabilityDom } from '../../test-helpers/reliabilityDom.mjs'
import { AdminVideoReview } from './AdminVideoReview'

vi.mock('../../api', async original => ({
  ...await original<typeof import('../../api')>(),
  getAdminVideos: vi.fn(),
}))
const data: AdminVideoPage = {
  days: 7, total: 1, offset: 0, limit: 24,
  healthSummary: { critical: 1, warning: 0, normal: 0 },
  items: [{
    artifactId: 'artifact-safe-id',
    attemptId: 'attempt-safe-id',
    taskId: 'owner-task-id',
    title: '其他账号生成的原片',
    username: 'brand-user',
    brandId: 'brand-id',
    brandName: 'Pagewings',
    createdAt: '2026-11-21T12:20:00Z',
    durationSeconds: 9.5,
    width: 1080,
    height: 1920,
    variant: 'STABLE',
    provider: 'kling',
    validationStatus: 'PASSED',
    finalApprovalStatus: 'PENDING',
    health: {
      severity: 'critical',
      qualityStatus: 'not_reviewed',
      primary: {
        code: 'CUT_FAILED', level: 'critical', title: '关联送剪任务失败',
        stage: 'CUT 送剪', detail: '最新送剪记录失败',
        nextStep: '核对CUT回执', evidence: 'multi_cutflow_task.status',
      },
      reasons: [{
        code: 'CUT_FAILED', level: 'critical', title: '关联送剪任务失败',
        stage: 'CUT 送剪', detail: '最新送剪记录失败',
        nextStep: '核对CUT回执', evidence: 'multi_cutflow_task.status',
      }],
      message: '有明确失败记录，请优先核实',
    },
  }],
}
let view: Awaited<ReturnType<typeof mount>>
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.mocked(getAdminVideos).mockResolvedValue(data)
})
afterEach(async () => {
  await view?.unmount()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
})
afterAll(teardownReliabilityDom)


it('keeps severity filters disabled when running backend has not activated health fields', async () => {
  vi.mocked(getAdminVideos).mockResolvedValue({
    ...data,
    healthSummary: undefined,
    items: data.items.map(item => ({ ...item, health: undefined })),
  })
  view = await mount(<AdminVideoReview adminId="administrator-id" />)
  await act(async () => { await Promise.resolve() })
  expect(view.host.textContent).toContain('异常筛选和排序暂不可用')
  expect(view.host.querySelector<HTMLSelectElement>('select[aria-label="视频排序"]')?.disabled).toBe(true)
  const dangerous = [...view.host.querySelectorAll<HTMLButtonElement>('button')]
    .find(button => button.textContent?.includes('严重异常'))
  expect(dangerous?.disabled).toBe(true)
})

it('shows an owner-labelled video and a review control without any editing action', async () => {
  view = await mount(<AdminVideoReview adminId="administrator-id" />)
  await act(async () => { await Promise.resolve() })
  expect(getAdminVideos).toHaveBeenCalledWith('administrator-id', 7, 0, '', '', 'all', 'priority')
  expect(view.host.textContent).toContain('其他账号生成的原片')
  expect(view.host.textContent).toContain('Pagewings · brand-user')
  expect(view.host.querySelectorAll('.admin-video-card')).toHaveLength(1)
  expect(view.host.querySelector('.admin-video-card img')?.getAttribute('src')).toContain('artifact-safe-id/thumbnail')
  expect(view.host.textContent).toContain('只读巡检')
  expect(view.host.textContent).toContain('关联送剪任务失败')
  expect(view.host.textContent).toContain('核对CUT回执')
  expect(view.host.querySelector('.admin-video-health-critical')).not.toBeNull()
  expect(view.host.querySelector('.admin-video-health-badge.is-critical')).not.toBeNull()
  const warningButton = [...view.host.querySelectorAll<HTMLButtonElement>('button')]
    .find(button => button.textContent?.includes('需要关注'))
  expect(warningButton).toBeDefined()
  await act(async () => { warningButton?.click(); await Promise.resolve() })
  expect(getAdminVideos).toHaveBeenLastCalledWith('administrator-id', 7, 0, '', '', 'warning', 'priority')
})
