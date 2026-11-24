import { act } from 'react'
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  fetchAdminCutObjectUrl, fetchAdminReferenceObjectUrl, fetchAdminVideoObjectUrl,
  getAdminVideoTasks, getAdminVideoTrace,
} from '../../api'
import type { AdminVideoRecord, AdminVideoTaskPage, AdminVideoTrace } from '../../types'
import { mount } from '../../test-helpers/reactHarness'
import { teardownReliabilityDom } from '../../test-helpers/reliabilityDom.mjs'
import { AdminVideoTaskReview } from './AdminVideoTaskReview'

vi.mock('../../api', async original => ({
  ...await original<typeof import('../../api')>(),
  getAdminVideoTasks: vi.fn(),
  getAdminVideoTrace: vi.fn(),
  fetchAdminCutObjectUrl: vi.fn(),
  fetchAdminReferenceObjectUrl: vi.fn(),
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
  vi.mocked(fetchAdminReferenceObjectUrl).mockResolvedValue('blob:reference-preview')
  vi.mocked(fetchAdminVideoObjectUrl).mockRejectedValue(new Error('test video unavailable'))
})
afterEach(async () => {
  await view?.unmount()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
})
afterAll(teardownReliabilityDom)


function sampleTrace(clipId: string): AdminVideoTrace {
  return {
    artifactId: clipId, taskId: 'task-a', attemptId: `attempt-${clipId}`, variantId: `variant-${clipId}`,
    taskCreatedAt: '2026-11-23T04:00:00Z',
    prompt: { savedText: null, savedHash: null, provenance: 'saved_variant_text_not_immutable_submission_snapshot',
      plan: {}, planner: 'sol', compiledBy: null },
    direction: {
      generationMode: 'automatic', directorMode: 'reference_guided', cutMode: 'hard_cut',
      smartStoryboard: true, multiShot: true, durationSeconds: 10,
      model: 'kling-3.0-omni', provider: 'kling', providerBackend: 'official',
      variantIndex: 1, referenceCount: 2,
    },
    references: [
      { id: `product-${clipId}`, filename: 'product.png', role: 'PRODUCT',
        imageLabel: 'image_1', order: 1, usedForGeneration: true,
        bindingEvidence: 'saved_binding', available: true },
      { id: `frame-${clipId}`, filename: 'first-frame.png', role: 'STORYBOARD',
        imageLabel: 'image_2', order: 2, usedForGeneration: true,
        bindingEvidence: 'task_asset_only', available: true },
    ],
    timeline: [{ label: '人工/自动确认方案', at: '2026-11-23T04:00:30Z',
      endAt: null, elapsedSeconds: null, status: null,
      source: 'generation_variant.confirmed_at', origin: 'persisted' }],
    durations: [
      { label: '任务创建至原片记录创建（总跨度）', seconds: 440, confidence: 'boundary' },
      { label: '方案确认至供应商提交（含准备与排队）', seconds: 90, confidence: 'boundary' },
      { label: '云端生成至完成（含轮询）', seconds: 300, confidence: 'boundary' },
      { label: '云端完成至原片记录建立', seconds: 20, confidence: 'boundary' },
      { label: '分析累计耗时（系统记录）', seconds: 15, confidence: 'aggregate' },
    ],
    taskStatus: 'COMPLETED', attemptStatus: 'COMPLETED', downloadStatus: 'READY',
    cut: [], validation: [], limitations: [],
    klingWireAudit: null,
  }
}

it('shows one production timeline, saved references and parameter tabs for each raw clip', async () => {
  const richTrace = (id: string) => {
    const trace = sampleTrace(id)
    trace.timeline = [
      ...trace.timeline,
      { label: '提交', at: '2026-11-23T04:02:00Z', endAt: null, elapsedSeconds: null,
        source: 'generation_attempt.submitted_at', status: null, origin: 'persisted' },
      { label: '完成', at: '2026-11-23T04:06:00Z', endAt: null, elapsedSeconds: null,
        source: 'generation_attempt.completed_at', status: null, origin: 'persisted' },
      { label: '保存', at: '2026-11-23T04:07:00Z', endAt: null, elapsedSeconds: null,
        source: 'download_artifact.created_at', status: null, origin: 'persisted' },
    ]
    return trace
  }
  vi.mocked(getAdminVideoTrace).mockImplementation(async (_admin, id) => richTrace(id))
  vi.mocked(getAdminVideoTasks).mockResolvedValue({
    ...data,
    items: [{ ...data.items[0], clips: data.items[0].clips.map((clip, i) => i === 0
      ? { ...clip, health: { severity: 'warning' as const, qualityStatus: 'not_reviewed' as const,
        primary: null, message: '生成偏长',
        reasons: [{ code: 'SLOW_PROVIDER', level: 'warning' as const, title: '生成偏长',
          stage: 'Kling 云端', detail: '明显偏长', evidence: 'generation_attempt',
          nextStep: '检查时间' }] } } : clip) }],
  })
  view = await mount(<AdminVideoTaskReview adminId="admin-id"/>)
  await act(async () => { await Promise.resolve() })
  const open = [...view.host.querySelectorAll('button')].find(el => el.textContent?.includes('查看完整详情'))
  await act(async () => { open?.click(); await Promise.resolve(); await Promise.resolve() })
  expect(view.host.querySelectorAll('.admin-task-video')).toHaveLength(1)
  expect(view.host.querySelectorAll('.admin-review-timeline')).toHaveLength(1)
  expect(view.host.querySelectorAll('.admin-review-step')).toHaveLength(8)
  expect(view.host.querySelectorAll('.admin-review-segment')).toHaveLength(8)
  const submitted = [...view.host.querySelectorAll<HTMLButtonElement>('.admin-review-segment')].find(b => b.textContent?.includes('Kling 提交'))
  await act(async () => { submitted?.click() })
  expect(submitted?.getAttribute('aria-pressed')).toBe('true')
  expect(view.host.querySelector('.admin-review-selected time')?.getAttribute('datetime')).toBe('2026-11-23T04:02:00Z')
  expect(view.host.querySelectorAll('.admin-review-metrics>div')).toHaveLength(3)
  expect(view.host.querySelector('.admin-review-step[data-tone="warning"]')).not.toBeNull()
  expect(view.host.querySelector('.admin-task-parameter-disclosure')).not.toBeNull()
  expect(view.host.querySelector<HTMLDetailsElement>('.admin-task-parameter-disclosure')?.open).toBe(false)
  expect(view.host.querySelectorAll('.admin-task-reference-card')).toHaveLength(2)
  expect(view.host.textContent).toContain('商品图')
  expect(view.host.textContent).toContain('分镜 / 首帧图')
  expect(view.host.textContent).toContain('kling-3.0-omni')
  expect(fetchAdminReferenceObjectUrl).toHaveBeenCalledWith('admin-id', 'clip-1', 'product-clip-1')
  const frame = view.host.querySelector('.admin-task-video-frame')
  const disclosure = view.host.querySelector<HTMLDetailsElement>('.admin-task-parameter-disclosure')!
  disclosure.open = true
  const secondTab = [...view.host.querySelectorAll<HTMLButtonElement>('.admin-task-clip-switch button')][1]
  await act(async () => { secondTab.click(); await Promise.resolve() })
  expect(fetchAdminReferenceObjectUrl).toHaveBeenCalledWith('admin-id', 'clip-2', 'frame-clip-2')
  expect(view.host.querySelectorAll('.admin-task-reference-card')).toHaveLength(2)
  const rawB = [...view.host.querySelectorAll<HTMLButtonElement>('.admin-task-media-strip button')]
    .find(b => b.textContent?.includes('Kling 原片 B'))
  await act(async () => { rawB?.click(); await Promise.resolve() })
  expect(view.host.querySelector('.admin-task-media-strip button.is-active')?.textContent).toContain('Kling 原片 B')
  expect(view.host.querySelector('.admin-task-video-frame')).toBe(frame)
  expect(view.host.querySelector('.admin-task-parameter-disclosure')).toBe(disclosure)
  expect(disclosure.open).toBe(true)
  expect(view.host.querySelector('.admin-task-video-frame img')?.getAttribute('src')).toContain('clip-2/thumbnail')
  const rawA = view.host.querySelectorAll<HTMLButtonElement>('.admin-task-media-strip button')[1]
  await act(async () => { rawA.click(); await Promise.resolve() })
  expect(view.host.querySelector('.admin-task-video-frame')).toBe(frame)
  expect(view.host.querySelector('.admin-task-video-frame img')?.getAttribute('src')).toContain('clip-1/thumbnail')
})

it('does not invent timestamps and marks confirmed CUT failures in red', async () => {
  const missing = sampleTrace('clip-1')
  missing.references = []
  missing.timeline = []
  missing.taskCreatedAt = null
  vi.mocked(getAdminVideoTrace).mockResolvedValue(missing)
  vi.mocked(getAdminVideoTasks).mockResolvedValue({
    ...data, items: [{ ...data.items[0], clips: [raw('clip-1', 1)], cut: null, cutStatus: 'FAILED' }],
  })
  view = await mount(<AdminVideoTaskReview adminId="admin-id"/>)
  await act(async () => { await Promise.resolve() })
  const open = [...view.host.querySelectorAll('button')].find(el => el.textContent?.includes('查看完整详情'))
  await act(async () => { open?.click(); await Promise.resolve() })
  expect(view.host.textContent).toContain('没有可追溯的生成参考图')
  expect(view.host.querySelectorAll('.admin-review-step[data-tone="missing"]').length).toBeGreaterThan(4)
  expect(view.host.querySelector('.admin-review-step[data-tone="critical"] strong')?.textContent).toBe('CUT 异常')
  expect(view.host.textContent).not.toContain('成片已保存')
})
it('shows one task card for two raw clips and launches the real CUT playback', async () => {
  view = await mount(<AdminVideoTaskReview adminId="admin-id"/>)
  await act(async () => { await Promise.resolve() })
  expect(view.host.querySelectorAll('.admin-task-card')).toHaveLength(1)
  expect(view.host.querySelector('.admin-task-card .admin-video-preview')?.getAttribute('data-orientation')).toBe('portrait')
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

it('keeps 9:16 as one main preview with available raw clips and prompts', async () => {
  view = await mount(<AdminVideoTaskReview adminId="admin-id"/>)
  await act(async () => { await Promise.resolve() })
  const button = [...view.host.querySelectorAll('button')].find(el => el.textContent?.includes('查看完整详情'))
  await act(async () => { button?.click(); await Promise.resolve() })
  expect(view.host.querySelectorAll('.admin-task-video')).toHaveLength(1)
  expect(view.host.querySelectorAll('.admin-task-media-strip button')).toHaveLength(3)
  expect(view.host.querySelectorAll('.admin-review-timeline')).toHaveLength(1)
  expect(view.host.textContent).toContain('已保存编排提示词')
  expect(view.host.textContent).toContain('Kling 实际接单提示词')
  expect(view.host.textContent).toContain('返回视频目录')
  expect(view.host.textContent).not.toContain('审核通过')
})

it('replays the legacy timestamp mismatch without inventing an eight-hour wait', async () => {
  const task = { ...data.items[0], createdAt: '2026-10-01T02:07:01', cut: null, cutStatus: 'READY' }
  const traceFor = (id: string) => {
    const trace = sampleTrace(id)
    trace.taskCreatedAt = '2026-10-01T02:00:00+00:00'
    trace.timeline = [
      ['parent_task.analysis_started_at', '2026-10-01T02:00:01.600000+00:00'],
      ['generation_variant.confirmed_at', '2026-10-01T02:01:28.000000+00:00'],
      ['generation_attempt.submitted_at', '2026-10-01T02:01:31.000000+00:00'],
      ['generation_attempt.completed_at', '2026-10-01T02:06:46.000000+00:00'],
      ['download_artifact.created_at', id === 'clip-1' ? '2026-10-01T02:07:01' : '2026-10-01T02:06:45+00:00'],
      ['multi_cutflow_task.created_at', '2026-10-01T02:07:07+00:00'],
    ].map(([source, at]) => ({ label: source, source, at, endAt: null, elapsedSeconds: null, status: null, origin: 'persisted' }))
    return trace
  }
  vi.mocked(getAdminVideoTasks).mockResolvedValue({ ...data, items: [task] })
  vi.mocked(getAdminVideoTrace).mockImplementation(async (_admin, id) => traceFor(id))
  view = await mount(<AdminVideoTaskReview adminId="admin-id"/>)
  await act(async () => { await Promise.resolve() })
  expect(view.host.textContent).toContain('原片保存 10/01 10:07')
  const open = [...view.host.querySelectorAll('button')].find(el => el.textContent?.includes('查看完整详情'))
  await act(async () => { open?.click(); await Promise.resolve(); await Promise.resolve() })
  const metrics = [...view.host.querySelectorAll('.admin-review-metrics strong')].map(el => el.textContent)
  expect(metrics).toEqual(['7分1秒', '6分59秒', '2秒'])
  const created = view.host.querySelector<HTMLButtonElement>('.admin-review-segment')
  await act(async () => { created?.click() })
  expect(view.host.querySelector('.admin-review-selected time')?.textContent).toBe('10/01 10:00:00')
  expect(view.host.querySelector('.admin-review-step[data-tone="warning"]')).toBeNull()
})

it('opens details from metadata and previews the original authenticated reference images', async () => {
  vi.mocked(getAdminVideoTrace).mockImplementation(async (_admin, id) => sampleTrace(id))
  view = await mount(<AdminVideoTaskReview adminId="admin-id"/>)
  await act(async () => { await Promise.resolve() })
  const body = view.host.querySelector<HTMLButtonElement>('.admin-task-detail-trigger')!
  expect(body.tagName).toBe('BUTTON')
  await act(async () => { body.querySelector('strong')?.click(); await Promise.resolve(); await Promise.resolve() })
  expect(view.host.querySelector('.admin-task-detail')).not.toBeNull()
  const times = view.host.querySelectorAll('.admin-review-milestone-time')
  expect(times[0].getAttribute('datetime')).toBe('2026-11-23T04:00:00Z')
  expect(times[0].textContent).toContain('12:00:00')
  expect(times[7].getAttribute('datetime')).toBe('2026-11-23T04:10:00Z')
  expect(times[7].textContent).toContain('12:10:00')
  const images = view.host.querySelectorAll<HTMLButtonElement>('.admin-reference-open')
  expect(images).toHaveLength(2)
  await act(async () => { images[0].click() })
  expect(document.body.querySelector('.admin-reference-preview img')?.getAttribute('src')).toBe('blob:reference-preview')
  await act(async () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })) })
  expect(document.body.querySelector('.admin-reference-preview')).toBeNull()
  await act(async () => { images[1].click() })
  expect(document.body.querySelector('.admin-reference-preview')?.getAttribute('aria-label')).toContain('首帧图')
  await act(async () => { document.body.querySelector<HTMLButtonElement>('.admin-reference-preview button[aria-label="关闭图片预览"]')?.click() })
  expect(document.body.querySelector('.admin-reference-preview')).toBeNull()
})
