import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../api'
import type { Task } from '../types'
import { taskCenterState } from './taskCenterState'

const base = {
  id: 'task-1',
  phase: 'DELIVERABLE',
  settings: { autoSubmitToCutflow: true },
  variants: [{ id: 'v1', generationStatus: 'SUCCEEDED', downloadArtifactId: 'asset-1' }],
} as unknown as Task

const batch = (status: string, outputs: number) => ({
  id: 'cut-1', status, updatedAt: '2026-11-11T06:00:00Z',
  sources: [{ taskId: 'task-1', variantId: 'v1' }],
  outputs: Array.from({ length: outputs }, (_, i) => ({ id: String(i) })),
} as unknown as MultiCutflowTask)

describe('task center: finished means final cut, not just raw video', () => {
  it('distinguishes no auto-cut receipt from a running editing task', () => {
    expect(taskCenterState(base, [])).toMatchObject({
      group: 'active', label: '等待创建送剪任务', manualSelection: false,
    })
  })
  it('shows a checking state until CUT history is read', () => {
    expect(taskCenterState(base, [], { loaded: false })).toMatchObject({
      group: 'active', label: '正在核对剪辑状态', manualSelection: false,
    })
  })
  it('requires a status refresh after CUT history query failure', () => {
    expect(taskCenterState(base, [], { loaded: false, error: true })).toMatchObject({
      group: 'attention', label: '剪辑状态同步异常', manualSelection: false,
    })
  })
  it('shows an accepted cut task as in progress without encouraging another submit', () => {
    expect(taskCenterState(base, [batch('QUEUED', 0)])).toMatchObject({
      group: 'active', label: '已创建送剪任务', manualSelection: false,
    })
  })
  it('surfaces a rejected notification without allowing another cut', () => {
    const rejected = { ...batch('READY', 0), errorCode: 'CUTFLOW_NOTIFICATION_TOO_LARGE',
      errorMessage: 'CUT HTTP 接单通知被拒绝（413：请求体过大）。无需重复送剪。' }
    expect(taskCenterState(base, [rejected])).toMatchObject({
      group: 'attention', label: 'CUT 通知超限，等待 NAS 接单',
      detail: 'CUT HTTP 接单通知被拒绝（413：请求体过大）。无需重复送剪。',
      manualSelection: false,
    })
    expect(taskCenterState(base, [{ ...rejected, status: 'IMPORTING', errorCode: null, errorMessage: null }]))
      .toMatchObject({ group: 'active', label: 'CUT 已接单，正在导入' })
  })
  it('does not mark a finished cut as done until outputs arrive', () => {
    expect(taskCenterState(base, [batch('PENDING_REVIEW', 0)]).group).toBe('active')
    expect(taskCenterState(base, [batch('PENDING_REVIEW', 1)])).toMatchObject({
      group: 'done', label: '成片可查看', outputs: 1,
    })
  })
  it('makes manual cut an explicit actionable state', () => {
    expect(taskCenterState({ ...base, settings: { autoSubmitToCutflow: false } } as Task, [])).toMatchObject({
      group: 'attention', label: '等待手动送剪', manualSelection: true,
    })
  })
  it('does not classify old finished raw-video tasks as editing in progress', () => {
    expect(taskCenterState({ ...base, settings: {} } as Task, [])).toMatchObject({
      group: 'attention', label: '原片待处理', manualSelection: false,
    })
  })
  it('keeps partial raw generation active even after one clip is ready', () => {
    const partial = { ...base, phase: 'GENERATING', variants: [base.variants[0], { id: 'v2', generationStatus: 'RUNNING', downloadArtifactId: null }] } as Task
    expect(taskCenterState(partial, [])).toMatchObject({ group: 'active', label: '正在生成原片' })
  })
  it('treats failed cut / unknown raw output as action needed', () => {
    expect(taskCenterState(base, [batch('FAILED', 0)]).group).toBe('attention')
    expect(taskCenterState({
      ...base, variants: [{ ...base.variants[0], generationStatus: 'UNKNOWN' }],
    } as Task, []).group).toBe('attention')
  })
})
