import { describe, expect, it } from 'vitest'
import type { AutoCutflowDispatchStatus } from '../../api'
import { cutflowDispatchPresentation } from './cutflowDispatchPresentation'

const snapshot = {
  taskId: 'task-1',
  state: 'BLOCKED',
  reasonCode: 'USD_DAILY_BUDGET_EXCEEDED',
  message: '今日美元预算剩余额度不足',
  worker: { state: 'online', heartbeatAt: null },
  batchId: null,
  batchStatus: null,
  requiredUsd: 0.30,
  remainingUsd: 0.10,
} satisfies AutoCutflowDispatchStatus

describe('automatic CUT budget admission status', () => {
  it('shows the reason and budget, not a false 90-second waiting message', () => {
    const result = cutflowDispatchPresentation(
      { autoSubmitToCutflow: true }, false, false, false,
      false, true, false, true, null, snapshot,
    )
    expect(result).toMatchObject({
      headline: '自动送剪受预算限制',
      stageLabel: '预算不足',
      tone: 'attention',
    })
    expect(result.detail).toContain('$0.10')
    expect(result.detail).toContain('$0.30')
  })

  it('distinguishes durable local send from verified CUT acceptance and queueing', () => {
    const saved = cutflowDispatchPresentation(
      { autoSubmitToCutflow: true }, true, true, false,
      false, true, false, true,
      { status: 'QUEUED', errorCode: null, errorMessage: null }, snapshot,
    )
    expect(saved).toMatchObject({ headline: '待送剪，正在准备素材', stageLabel: '待投递' })
    const awaitingAck = cutflowDispatchPresentation(
      { autoSubmitToCutflow: true }, true, true, false,
      false, true, false, true,
      { status: 'READY', errorCode: null, errorMessage: null }, snapshot,
    )
    expect(awaitingAck.headline).toBe('素材已送出，等待 CUT 接单')
    const accepted = cutflowDispatchPresentation(
      { autoSubmitToCutflow: true }, true, true, false,
      false, true, false, true,
      { status: 'ACCEPTED', errorCode: null, errorMessage: null }, snapshot,
    )
    expect(accepted).toMatchObject({ headline: 'CUT 已接单，排队中', stageLabel: 'CUT 排队中' })
  })

  it('never suppresses a confirmed editing record based on an old budget snapshot', () => {
    const result = cutflowDispatchPresentation(
      { autoSubmitToCutflow: true }, true, true, false,
      false, true, false, true,
      { status: 'EDITING', errorCode: null, errorMessage: null },
      snapshot,
    )
    expect(result.headline).toBe('CUT 正在剪辑')
  })
})
