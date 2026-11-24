import { describe, expect, it } from 'vitest'
import type { CutflowHandoff } from '../api'
import { cutflowPresentation, cutflowRecoveryHint, cutflowReviewLabel, cutflowStageLabel, cutflowTime, multiCutflowPresentation } from './cutflowPresentation'
import type { MultiCutflowTask } from '../api'

const handoff = (status: string, outputs: CutflowHandoff['outputs'] = []) => ({ status, outputs }) as CutflowHandoff
const multiTask = (status: string, outputs: MultiCutflowTask['outputs'] = []) => ({
  status,
  outputs,
} as unknown as MultiCutflowTask)

describe('cutflow progress evidence', () => {
  it('keeps automatic jobs on the source page while legacy jobs keep their configuration step', () => {
    expect(cutflowPresentation({ ...handoff('AWAITING_EDIT'), editMode: 'automatic' }))
      .toMatchObject({ label: '等待自动剪辑', step: 3, active: true })
    expect(cutflowPresentation(handoff('AWAITING_EDIT')).label).toBe('等待剪辑配置')
  })

  it('requires returned media before showing completed progress', () => {
    expect(cutflowPresentation(handoff('PENDING_REVIEW'))).toMatchObject({ step: 4, active: true, label: '等待成片回传' })
    expect(cutflowPresentation(handoff('PENDING_REVIEW', [{ id: 'render' } as CutflowHandoff['outputs'][number]])))
      .toMatchObject({ step: 5, active: false, label: '剪辑完成' })
  })

  it('keeps revisions in progress even when an earlier output exists', () => {
    expect(cutflowPresentation(handoff('EDITING', [{ id: 'previous' } as CutflowHandoff['outputs'][number]])))
      .toMatchObject({ step: 4, active: true, label: '正在剪辑' })
  })

  it('describes a submitted multi-source job without confusing it with an unedited source', () => {
    expect(multiCutflowPresentation(multiTask('READY')))
      .toMatchObject({ label: '素材已就绪，等待 CUT 接单', active: true, completed: false })
    expect(multiCutflowPresentation(multiTask('EDITING')).label)
      .toBe('CUT 正在剪辑')
    expect(multiCutflowPresentation(multiTask('PENDING_REVIEW', [{ id: 'render' } as MultiCutflowTask['outputs'][number]])))
      .toMatchObject({ label: '成片待审核', active: false, completed: true })
  })

  it('shows 413 and missing CUT receipt as pending intake problems, never completed', () => {
    const oversized = { ...multiTask('READY'), errorCode: 'CUTFLOW_NOTIFICATION_TOO_LARGE' }
    const missing = { ...multiTask('READY'), errorCode: 'CUTFLOW_STATUS_MISSING' }
    expect(multiCutflowPresentation(oversized)).toMatchObject({
      label: 'CUT 通知超限，等待 NAS 接单', active: true, completed: false,
    })
    expect(multiCutflowPresentation(missing)).toMatchObject({
      label: '等待 CUT 接单超时，正在核对', active: true, completed: false,
    })
    expect(multiCutflowPresentation({ ...oversized, status: 'IMPORTING' }).label).toBe('CUT 已接单，正在导入')
  })

  it.each(['FAILED', 'CANCELED', 'NEW_UNKNOWN_STATE'])('never presents %s as complete or active', status => {
    expect(cutflowPresentation(handoff(status))).toMatchObject({ step: 0, active: false })
  })

  it('retains a CUT job during NAS authentication loss and clears the warning after recovery', () => {
    const task = { ...multiTask('EDITING'), errorCode: 'CUTFLOW_NAS_AUTH_FAILED' }
    expect(multiCutflowPresentation(task)).toMatchObject({ label: '共享目录凭证失效，等待管理员恢复', active: true, completed: false })
    expect(multiCutflowPresentation({ ...task, errorCode: null }).label).toBe('CUT 正在剪辑')
    expect(multiCutflowPresentation({ ...task, status: 'PENDING_REVIEW', outputs: [{ id: 'render' } as MultiCutflowTask['outputs'][number]] }).completed).toBe(true)
  })

  it('keeps review decisions separate from rendering completion', () => {
    expect(cutflowReviewLabel('PENDING')).toBe('待审核')
    expect(cutflowReviewLabel('APPROVED')).toBe('审核通过')
    expect(cutflowReviewLabel('REJECTED')).toBe('审核未通过')
    expect(cutflowReviewLabel('UNKNOWN')).toBe('审核状态待确认')
  })

  it('explains stages and recovery without exposing unknown remote text', () => {
    expect(cutflowStageLabel('render')).toBe('渲染成片')
    expect(cutflowStageLabel('http://private/token')).toBe('尚未获得阶段信息')
    expect(cutflowRecoveryHint('CUTFLOW_MEDIA_UNAVAILABLE')).toContain('无需再次送入剪辑')
    expect(cutflowRecoveryHint('UNKNOWN')).toBe('')
    expect(cutflowTime('bad-date')).toBe('暂无记录')
    expect(cutflowTime(null)).toBe('暂无记录')
    expect(cutflowTime('2026-10-18 03:00:00')).toBe(cutflowTime('2026-10-18T03:00:00Z'))
  })
})
