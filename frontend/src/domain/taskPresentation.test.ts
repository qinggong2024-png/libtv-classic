import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import { taskFailureMessage } from './taskPresentation'

describe('task failure details', () => {
  it('shows known pre-submit Kling schema rejection instead of generic aggregate failure', () => {
    const task = {
      phase: 'ACTION_REQUIRED',
      phaseReasonCode: 'AGGREGATED_ACTION_REQUIRED',
      generationProvider: 'kling_official_api',
      variants: [{
        errorCode: 'KLING_API_VALIDATION_FAILED',
        errorMessage: "audio mode 'off' is not supported by the current model",
      }],
    } as Task
    expect(taskFailureMessage(task)).toContain('HTTP 400 / 1201')
    expect(taskFailureMessage(task)).not.toContain('检查素材')
  })
})
