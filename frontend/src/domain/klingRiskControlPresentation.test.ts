import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import { userFacingError } from '../errorPresentation'
import { isKlingRiskControlFailure, taskFailureMessage, taskRecoveryHeadline } from './taskPresentation'

function riskTask(code: string, message: string): Task {
  return {
    phase: 'ACTION_REQUIRED',
    phaseReasonCode: 'AGGREGATED_ACTION_REQUIRED',
    generationProvider: 'kling_official_api',
    variants: [{ errorCode: code, errorMessage: message }],
  } as Task
}

describe('Kling cloud risk-control failures', () => {
  it('labels an exact, current risk-control failure without blaming source quality', () => {
    const task = riskTask('KLING_REMOTE_RISK_CONTROL_REJECTED', 'Failure to pass the risk control system')
    expect(isKlingRiskControlFailure(task)).toBe(true)
    expect(taskRecoveryHeadline(task)).toContain('风控审核')
    expect(taskFailureMessage(task)).toContain('已受理')
    expect(taskFailureMessage(task)).toContain('暂勿直接重试')
    expect(taskFailureMessage(task)).not.toContain('请检查素材和设置')
    expect(userFacingError({ code: 'KLING_REMOTE_RISK_CONTROL_REJECTED' })).toContain('风控审核')
  })

  it('recognizes historic generic codes with the exact original provider message', () => {
    const task = riskTask('KLING_REMOTE_TASK_FAILED', 'Failure to pass the risk control system')
    expect(isKlingRiskControlFailure(task)).toBe(true)
    expect(taskRecoveryHeadline(task)).toContain('风控审核')
    expect(userFacingError({
      code: 'KLING_REMOTE_TASK_FAILED',
      message: 'Failure to pass the risk control system',
    })).toContain('风控审核')
  })

  it('does not misclassify unrelated failures as risk control', () => {
    const task = riskTask('KLING_REMOTE_TASK_FAILED', 'Remote execution error')
    expect(isKlingRiskControlFailure(task)).toBe(false)
    expect(taskFailureMessage(task)).not.toContain('风控审核')
    expect(userFacingError({
      code: 'KLING_REMOTE_TASK_FAILED',
      message: 'Remote execution error',
    })).not.toContain('风控审核')
  })
})
