import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../../api'
import { cutflowDispatchPresentation } from './cutflowDispatchPresentation'

const settings = { autoSubmitToCutflow: true }
const snapshot = (status: string, errorCode: string | null = null) =>
  ({ status, errorCode, errorMessage: null }) as MultiCutflowTask

describe('CUT stage proof and safe user-facing labels', () => {
  it.each([
    ['QUEUED', '待送剪，正在准备素材', '待投递'],
    ['ACCEPTED', 'CUT 已接单，排队中', 'CUT 排队中'],
    ['READY', '素材已送出，等待 CUT 接单', '等待接单'],
    ['IMPORTING', 'CUT 已接单，正在导入', '导入中'],
    ['EDITING', 'CUT 正在剪辑', '剪辑中'],
    ['UNKNOWN', 'CUT 状态待核实', '状态待核实'],
  ])('%s uses only confirmed evidence', (status, headline, stageLabel) => {
    const presentation = cutflowDispatchPresentation(
      settings, true, true, false, false, true, false, false, snapshot(status),
    )
    expect(presentation).toMatchObject({ headline, stageLabel })
  })

  it('marks an unconfirmed intake as a CUT handoff issue, not an edited video', () => {
    const result = cutflowDispatchPresentation(
      settings, true, true, false, false, true, false, false,
      snapshot('READY', 'CUTFLOW_STATUS_MISSING'),
    )
    expect(result).toMatchObject({ headline: 'CUT 接单异常', tone: 'attention' })
  })

  it('shows recoverable NAS authentication failure without a duplicate submission action', () => {
    const result = cutflowDispatchPresentation(settings, true, true, false, false, true, false, false,
      snapshot('EDITING', 'CUTFLOW_NAS_AUTH_FAILED'))
    expect(result).toMatchObject({ headline: '共享目录凭证失效，等待管理员恢复', action: null, tone: 'neutral' })
    expect(result.detail).toContain('无需重复送剪')
  })

  it('shows a sync error above a cached active record', () => {
    const result = cutflowDispatchPresentation(
      settings, true, true, false, false, true, true, false, snapshot('EDITING'),
    )
    expect(result).toMatchObject({ headline: '剪辑状态同步异常', tone: 'attention' })
  })

  it('does not convert a missing local receipt into proof of remote rejection', () => {
    const result = cutflowDispatchPresentation(settings, false, false, false, false, true, false, true)
    expect(result).toMatchObject({ headline: '自动送剪任务尚未建立', stageLabel: '待调度核查' })
    expect(result.detail).not.toContain('CUT 已拒绝')
  })
})
