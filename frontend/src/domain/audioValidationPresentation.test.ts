import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import { taskFailureMessage } from './taskPresentation'
import { variantExecutionMessage } from '../providerPresentation'

describe('successful generation with a rejected local delivery', () => {
  it('describes validation failure rather than an upstream generation failure', () => {
    const task = {
      phase: 'ACTION_REQUIRED',
      phaseReasonCode: 'AGGREGATED_ACTION_REQUIRED',
      generationProvider: 'kling_official_api',
      variants: [{ generationStatus: 'SUCCEEDED', downloadStatus: 'READY', validationStatus: 'FAILED' }],
    } as Task
    expect(taskFailureMessage(task)).toContain('已完成生成')
    expect(taskFailureMessage(task)).toContain('本地视频校验未通过')
    expect(taskFailureMessage(task)).toContain('无需再次提交付费生成')
  })

  it('does not show a ready message for an invalid downloaded clip', () => {
    const result = variantExecutionMessage({
      generationStatus: 'SUCCEEDED',
      downloadStatus: 'READY',
      validationStatus: 'FAILED',
      downloadArtifactId: 'existing-artifact',
      errorCode: null,
      provider: 'kling_official_api',
    })
    expect(result).toContain('本地成品校验未通过')
    expect(result).not.toBe('成品文件已就绪')
  })
})
