import { act } from 'react'
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getAdminVideoTrace } from '../../api'
import type { AdminVideoRecord, AdminVideoTrace } from '../../types'
import { mount } from '../../test-helpers/reactHarness'
import { teardownReliabilityDom } from '../../test-helpers/reliabilityDom.mjs'
import { AdminVideoTracePanel } from './AdminVideoTracePanel'

vi.mock('../../api', async original => ({
  ...await original<typeof import('../../api')>(),
  getAdminVideoTrace: vi.fn(),
}))

const item: AdminVideoRecord = {
  artifactId: 'saved-video-id',
  taskId: 'task-id',
  attemptId: 'attempt-id',
  title: '带参考图的原片',
  username: 'another-account',
  brandId: 'brand-1',
  brandName: 'Pagewings',
  createdAt: '2026-11-21T10:00:00Z',
  durationSeconds: 10,
  width: 1080,
  height: 1920,
  variant: 'STABLE',
  provider: 'kling',
  validationStatus: 'PASSED',
  finalApprovalStatus: 'PENDING',
}
const trace: AdminVideoTrace = {
  artifactId: 'saved-video-id',
  taskId: 'task-id',
  attemptId: 'attempt-id',
  variantId: 'variant-id',
  taskCreatedAt: '2026-11-21T09:00:00Z',
  prompt: {
    savedText: 'Saved prompt text, not a submission snapshot',
    savedHash: 'test-hash',
    provenance: 'saved_variant_text_not_immutable_submission_snapshot',
    plan: { creativeIdea: 'Visual contrast', masterPrompt: 'Original plan draft' },
    planner: 'Sol',
    compiledBy: 'sol-director',
  },
  direction: {
    generationMode: 'automatic',
    directorMode: 'reference_guided',
    cutMode: 'explicit_cuts',
    smartStoryboard: true,
    multiShot: true,
    durationSeconds: 10,
    model: 'kling',
    provider: 'kling',
    providerBackend: 'api',
    variantIndex: 1,
    referenceCount: 1,
  },
  references: [{
    id: 'asset-1', filename: 'first-frame.png', role: 'STORYBOARD',
    imageLabel: 'image_1', order: 1, bindingEvidence: 'saved_binding',
    usedForGeneration: true, available: false,
  }],
  timeline: [{
    label: '供应商确认提交', at: '2026-11-21T09:01:00+00:00',
    endAt: '2026-11-21T09:02:14+00:00',
    elapsedSeconds: 74, status: 'completed',
    source: 'generation_attempt.submitted_at', origin: 'persisted',
  }],
  durations: [
    { label: '任务创建至原片记录创建（总跨度）', seconds: 132, confidence: 'boundary' },
    { label: '云端生成至完成（含轮询）', seconds: 74, confidence: 'boundary' },
  ],
  taskStatus: 'DELIVERABLE',
  attemptStatus: 'SUCCEEDED',
  downloadStatus: 'READY',
  cut: [],
  validation: [],
  limitations: ['历史没有不可变的请求快照。'],
}

let view: Awaited<ReturnType<typeof mount>>
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.mocked(getAdminVideoTrace).mockResolvedValue(trace)
})
afterEach(async () => {
  await view?.unmount()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
})
afterAll(teardownReliabilityDom)

it('separates saved prompt from submission evidence and shows references and measured times', async () => {
  view = await mount(<AdminVideoTracePanel adminId="platform-admin" item={item}
    onPlay={() => {}} onClose={() => {}} />)
  await act(async () => { await Promise.resolve() })
  expect(getAdminVideoTrace).toHaveBeenCalledWith('platform-admin', 'saved-video-id')
  expect(document.body.textContent).toContain('遵循参考图')
  expect(document.body.textContent).toContain('Saved prompt text')
  expect(document.body.textContent).toContain('不能确认图片或分镜是否实际转换')
  expect(document.body.textContent).toContain('image_1')
  expect(document.body.textContent).toContain('供应商确认提交')
  expect(document.body.textContent).toContain('1 分 14 秒')
  expect(document.body.textContent).toContain('未找到与这条原片直接关联的 CUT')
})


it('shows only evidence-backed Kling image and shot conversion results', async () => {
  vi.mocked(getAdminVideoTrace).mockResolvedValue({
    ...trace,
    klingWireAudit: {
      evidence: 'provider_accepted_wire_snapshot',
      recordedAtUtc: '2026-11-22T05:00:00Z',
      submissionState: 'PROVIDER_ACCEPTED',
      imageReferenceConversion: 'converted',
      imageReferenceSourceConversion: 'converted',
      imageReferenceSourceAliasesChanged: 2,
      imageReferenceBindingVerified: true,
      nativeShotConversion: 'converted_to_native_shots',
      exactKlingPrompt: 'shot 1, 3, @image_1正面; shot 2, 7, @image_2印花;',
    },
  })
  view = await mount(<AdminVideoTracePanel adminId="platform-admin" item={item}
    onPlay={() => {}} onClose={() => {}} />)
  await act(async () => { await Promise.resolve() })
  expect(document.body.textContent).toContain('Kling 实际提交证据')
  expect(document.body.textContent).toContain('已转换为 @image_N')
  expect(document.body.textContent).toContain('已转换为原生 shot 格式')
  expect(document.body.textContent).toContain('Sol 原文被规范化的图片别名：2 处')
  expect(document.body.textContent).not.toContain('不能确认图片或分镜是否实际转换')
  expect(document.body.textContent).toContain('查看真正发送给 Kling 的提示词')
})
