import { describe, expect, it } from 'vitest'
import { type Task } from '../types'
import { isWaveSpeedBalanceFailure, taskFailureMessage, taskRecoveryHeadline } from './taskPresentation'
import { variantExecutionMessage } from '../providerPresentation'

function balanceTask(status: string, code: string, message: string): Task {
  return {
    phase: 'ACTION_REQUIRED',
    workflowKind: 'DEEP_VIDEO',
    generationProvider: 'wavespeed_video',
    settings: { deepVideoStage: 'replacement' },
    variants: [{
      generationStatus: status,
      errorCode: code,
      errorMessage: message,
      provider: 'wavespeed_video',
      providerErrorDetails: null,
    }],
  } as unknown as Task
}

describe('WaveSpeed deep-video balance recovery', () => {
  it('classifies an existing HTTP 400 unknown task by its actual error', () => {
    const task = balanceTask('UNKNOWN', 'WAVESPEED_VALIDATION_FAILED', 'Insufficient credits. Please top up.')
    expect(isWaveSpeedBalanceFailure(task)).toBe(true)
    expect(taskRecoveryHeadline(task)).toBe('深度提取成功 · 替换阶段余额不足')
    expect(taskFailureMessage(task)).toContain('深度视频已经提取并保存')
    expect(taskFailureMessage(task)).toContain('无需再次支付深度提取费用')
    expect(taskFailureMessage(task)).not.toContain('云端任务状态暂未核实')
  })

  it('displays the exact quote and remaining balance when the preflight returned them', () => {
    const task = balanceTask('FAILED', 'WAVESPEED_BALANCE_INSUFFICIENT', 'insufficient credits')
    task.variants[0].providerErrorDetails = {
      balanceUsd: 2.1421, estimatedPriceUsd: 2.16, shortfallUsd: 0.0179,
    }
    expect(taskFailureMessage(task)).toContain('$2.1421')
    expect(taskFailureMessage(task)).toContain('$2.1600')
    expect(taskFailureMessage(task)).toContain('$0.0179')
    expect(variantExecutionMessage(task.variants[0], task.generationProvider, task.phase))
      .toContain('WaveSpeed 账户余额不足')
  })

  it('does not misclassify genuine unknown network failures as balance errors', () => {
    const task = balanceTask('UNKNOWN', 'WAVESPEED_NETWORK_ERROR', 'network timeout')
    expect(isWaveSpeedBalanceFailure(task)).toBe(false)
    expect(taskRecoveryHeadline(task)).toBe('生成暂时无法继续')
    expect(taskFailureMessage(task)).toContain('任务状态暂未核实')
  })
})
