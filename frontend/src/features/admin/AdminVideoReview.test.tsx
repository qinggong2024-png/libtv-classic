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
    .find(button => button.textContent?.includes('明确异常'))
  expect(dangerous).toBeUndefined()
  expect(view.host.querySelector('.admin-video-attention')).toBeNull()
})


it('leaves a normal video visually quiet while keeping review access', async () => {
  vi.mocked(getAdminVideos).mockResolvedValue({
    ...data,
    healthSummary: { critical: 0, warning: 0, normal: 1 },
    items: data.items.map(item => ({
      ...item,
      health: {
        severity: 'normal',
        primary: null,
        reasons: [],
        qualityStatus: 'not_reviewed',
        message: '暂未发现链路异常，仍需要人工审核',
      },
    })),
  })
  view = await mount(<AdminVideoReview adminId="administrator-id" />)
  await act(async () => { await Promise.resolve() })
  expect(view.host.querySelector('.admin-video-attention')).not.toBeNull()
  expect(view.host.querySelectorAll('.admin-video-card')).toHaveLength(1)
  expect(view.host.querySelector('.admin-video-card')?.className).toBe('admin-video-card')
  expect(view.host.querySelector('.admin-video-issue')).toBeNull()
  expect(view.host.querySelector('.admin-video-health-badge')).toBeNull()
  expect(view.host.querySelector('.admin-video-preview[aria-label^="播放视频"]')).not.toBeNull()
  expect(view.host.textContent).toContain('查看生产链路')
  expect(view.host.textContent).not.toContain('暂未发现链路异常 · 画面仍需人工检查')
})

it('shows a warning as one understated explanation row instead of multiple badges', async () => {
  vi.mocked(getAdminVideos).mockResolvedValue({
    ...data,
    healthSummary: { critical: 0, warning: 1, normal: 0 },
    items: data.items.map(item => ({
      ...item,
      health: {
        severity: 'warning',
        primary: {
          code: 'SLOW_PROVIDER',
          level: 'warning',
          title: '云端生成与轮询明显偏长',
          stage: '云端生成与轮询',
          detail: '耗时超过可比较样本',
          nextStep: '查看上下游时间节点',
          evidence: 'generation_attempt.submitted_at/completed_at',
        },
        reasons: [{
          code: 'SLOW_PROVIDER',
          level: 'warning',
          title: '云端生成与轮询明显偏长',
          stage: '云端生成与轮询',
          detail: '耗时超过可比较样本',
          nextStep: '查看上下游时间节点',
          evidence: 'generation_attempt.submitted_at/completed_at',
        }],
        qualityStatus: 'not_reviewed',
        message: '有可疑环节，建议检查',
      },
    })),
  })
  view = await mount(<AdminVideoReview adminId="administrator-id" />)
  await act(async () => { await Promise.resolve() })
  expect(view.host.querySelectorAll('.admin-video-issue')).toHaveLength(1)
  expect(view.host.querySelector('.admin-video-issue svg.is-warning')).not.toBeNull()
  expect(view.host.querySelector('.admin-video-issue summary')?.getAttribute('title')).toBeNull()
  expect(view.host.querySelector('.admin-video-issue')?.getAttribute('title')).toContain('查看上下游时间节点')
  expect(view.host.querySelector('.admin-video-health-badge')).toBeNull()
  const warningQueue = [...view.host.querySelectorAll<HTMLButtonElement>('.admin-video-attention-row')]
    .find(button => button.textContent?.includes('1 条需要检查'))
  await act(async () => { warningQueue?.click(); await Promise.resolve() })
  expect(getAdminVideos).toHaveBeenLastCalledWith('administrator-id', 7, 0, '', '', 'warning', 'priority')
})

it('replaces opaque historical generation hashes with readable clip labels', async () => {
  const opaque = 'ccad70e00dc676af8c54d3d757a62057f14521d74e753ad5b2ec0c5a43ced089-output'
  vi.mocked(getAdminVideos).mockResolvedValue({
    ...data,
    items: data.items.map(item => ({ ...item, title: opaque })),
  })
  view = await mount(<AdminVideoReview adminId="administrator-id" />)
  await act(async () => { await Promise.resolve() })
  expect(view.host.querySelector('.admin-video-card-body > strong')?.textContent).toContain('Pagewings · 原片')
  expect(view.host.querySelector('.admin-video-card-body > strong')?.textContent).not.toContain('ccad70e00d')
  expect(view.host.querySelector('.admin-video-card-body > strong')?.getAttribute('title')).toBe(opaque)
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
  expect(view.host.querySelector('.admin-video-health-badge')).toBeNull()
  expect(view.host.querySelector('.admin-video-health-critical')).toBeNull()
  expect(view.host.querySelector('.admin-video-card')?.className).toBe('admin-video-card')
  expect(view.host.querySelector('.admin-video-issue summary')?.textContent).toContain('关联送剪任务失败')
  const disclosure = view.host.querySelector<HTMLDetailsElement>('.admin-video-issue')
  expect(disclosure?.open).toBe(false)
  await act(async () => { disclosure?.querySelector('summary')?.click() })
  expect(disclosure?.open).toBe(true)
  expect(view.host.textContent).toContain('发生环节：CUT 送剪')
  const criticalButton = [...view.host.querySelectorAll<HTMLButtonElement>('.admin-video-attention-row')]
    .find(button => button.textContent?.includes('1 条明确异常'))
  expect(criticalButton).toBeDefined()
  await act(async () => { criticalButton?.click(); await Promise.resolve() })
  expect(getAdminVideos).toHaveBeenLastCalledWith('administrator-id', 7, 0, '', '', 'critical', 'priority')
})
