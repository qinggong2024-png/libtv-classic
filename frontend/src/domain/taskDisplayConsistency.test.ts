import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import { taskCenterState } from './taskCenterState'
import {
  activeTaskRecoveryPresentation,
  taskHasActionableReason,
  taskStatusPresentation,
} from './statusPresentation'

function task(
  phase: string,
  phaseReasonCode: string | null,
  variants: Array<{ id: string; generationStatus: string; downloadArtifactId: string | null; providerStatus?: string }> = [
  { id: 'v1', generationStatus: 'RUNNING', downloadArtifactId: null },
  { id: 'v2', generationStatus: 'PENDING', downloadArtifactId: null },
]): Task {
  return {
    id: 'task-status-consistency',
    phase,
    phaseReasonCode,
    settings: { autoSubmitToCutflow: false },
    variants,
    actionCapabilities: { canConfirm: false, canDownload: false, canRetryAnalysis: false },
  } as unknown as Task
}

describe('原片状态在任务中心和详情中保持一致', () => {
  it('正在生成时即使残留错误码，也不显示红色失败说明', () => {
    const current = task('GENERATING', 'KLING_API_SUBMISSION_FAILED')
    expect(taskHasActionableReason(current)).toBe(false)
    expect(taskCenterState(current, [])).toMatchObject({
      group: 'active',
      label: '正在生成原片',
      readyCount: 0,
    })
    expect(taskStatusPresentation(current).semantic).toBe('processing')
  })

  it('已成功下载两条原片后，旧错误码不能冒充当前故障', () => {
    const completed = task('DELIVERABLE', 'KLING_API_SUBMISSION_FAILED', [
      { id: 'v1', generationStatus: 'SUCCEEDED', downloadArtifactId: 'mp4-1' },
      { id: 'v2', generationStatus: 'SUCCEEDED', downloadArtifactId: 'mp4-2' },
    ])
    expect(taskHasActionableReason(completed)).toBe(false)
    expect(taskCenterState(completed, [])).toMatchObject({ readyCount: 2 })
    expect(taskStatusPresentation(completed).semantic).toBe('completed')
  })

  it('真正需要人工介入的失败仍保留红字与处理入口', () => {
    const failed = task('ACTION_REQUIRED', 'KLING_API_SUBMISSION_FAILED')
    expect(taskHasActionableReason(failed)).toBe(true)
    expect(taskCenterState(failed, [])).toMatchObject({ group: 'attention', label: '需要处理' })
    expect(taskStatusPresentation(failed).semantic).toBe('manual')
  })

  it('余额不足在列表和详情中统一标记为等待恢复，不误报成生成中', () => {
    const waiting = task('GENERATING', 'KLING_API_BALANCE_INSUFFICIENT', [
      { id: 'v1', generationStatus: 'SUCCEEDED', downloadArtifactId: 'mp4-1' },
      { id: 'v2', generationStatus: 'PENDING', providerStatus: 'waiting_balance', downloadArtifactId: null },
    ])
    const recovery = activeTaskRecoveryPresentation(waiting)
    expect(recovery?.label).toBe('API 余额不足')
    expect(taskCenterState(waiting, [])).toMatchObject({
      group: 'active', label: recovery?.label, detail: recovery?.description, readyCount: 1,
    })
    expect(taskStatusPresentation(waiting).label).toBe(recovery?.label)
    expect(taskHasActionableReason(waiting)).toBe(false)
  })

  it('编排服务自动重试使用相同提示，不冒充已经调用视频生成', () => {
    const waiting = task('ANALYZING', 'AI_CREATIVE_RETRY_SCHEDULED', [])
    const recovery = activeTaskRecoveryPresentation(waiting)
    expect(taskCenterState(waiting, [])).toMatchObject({
      group: 'active', label: recovery?.label, detail: recovery?.description,
    })
    expect(taskStatusPresentation(waiting).label).toBe('AI 编排等待自动重试')
    expect(taskHasActionableReason(waiting)).toBe(false)
  })

  it('准备与排队阶段如实显示各自阶段，而不是生成中', () => {
    expect(taskCenterState(task('PREPARING', null), []).label).toBe('正在准备生成')
    expect(taskCenterState(task('QUEUED', null), []).label).toBe('等待生成')
  })
})
