import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import type { MultiCutflowTask } from '../api'
import { isTaskAttentionAcknowledged, taskAttentionGroup, taskAttentionSignature } from './taskAttention'

const task = (phase = 'ACTION_REQUIRED') => ({
  id: 'task-01',
  phase,
  phaseReasonCode: 'PROVIDER_REJECTED',
  settings: { autoSubmitToCutflow: true },
  variants: [
    { id: 'v2', generationStatus: 'FAILED', errorCode: 'MODERATION_REJECTED' },
    { id: 'v1', generationStatus: 'SUCCEEDED', downloadArtifactId: 'raw-1' },
  ],
  updatedAt: '2026-11-21T00:01:00Z',
}) as unknown as Task
const cut = (status: string) => ({
  id: 'cut-1', status, updatedAt: '2026-11-21T00:00:00Z',
  sources: [{ taskId: 'task-01', variantId: 'v1' }],
  outputs: [], errorCode: status === 'FAILED' ? 'CUT_FAILED' : null,
}) as unknown as MultiCutflowTask

describe('task attention is a user acknowledgement, not a generation phase', () => {
  it('uses one canonical classifier for cancelled, partially completed and failed tasks', () => {
    expect(taskAttentionGroup(task('CANCELLED'), [])).toBe('cancelled')
    expect(taskAttentionGroup(task('PARTIALLY_DELIVERABLE'), [])).toBe('done')
    expect(taskAttentionGroup(task('ACTION_REQUIRED'), [])).toBe('attention')
    expect(taskAttentionGroup(task('GENERATING'), [])).toBe('active')
  })

  it('acknowledges a specific incident; timestamps and list ordering do not bring back the red dot', () => {
    const original = task()
    const fingerprint = taskAttentionSignature(original, [cut('FAILED')])
    const map = { 'task-01': fingerprint }
    expect(isTaskAttentionAcknowledged(original.id, fingerprint, map)).toBe(true)
    expect(taskAttentionSignature({ ...original, updatedAt: '2026-12-01T00:00:00Z',
      variants: [...original.variants].reverse() } as Task, [cut('FAILED')])).toBe(fingerprint)
  })

  it('restores attention when the underlying generation or CUT incident changes', () => {
    const original = task()
    const fingerprint = taskAttentionSignature(original, [cut('FAILED')])
    const updatedVariant = { ...original, variants: original.variants.map(variant => variant.id === 'v2'
      ? { ...variant, generationStatus: 'UNKNOWN' } : variant) } as Task
    expect(taskAttentionSignature(updatedVariant, [cut('FAILED')])).not.toBe(fingerprint)
    expect(taskAttentionSignature(original, [cut('READY')])).toBe(fingerprint) // unrelated CUT versions cannot revive a dismissed generation failure
    expect(taskAttentionSignature(task('DELIVERABLE'), [cut('READY')]))
      .not.toBe(taskAttentionSignature(task('DELIVERABLE'), [cut('FAILED')]))
    expect(isTaskAttentionAcknowledged(original.id, taskAttentionSignature(updatedVariant, [cut('READY')]),
      { 'task-01': fingerprint })).toBe(false)
  })

  it('neither a missing acknowledgement nor another task id suppresses a task', () => {
    const fingerprint = taskAttentionSignature(task(), [])
    expect(isTaskAttentionAcknowledged('task-01', fingerprint, {})).toBe(false)
    expect(isTaskAttentionAcknowledged('task-02', fingerprint, { 'task-01': fingerprint })).toBe(false)
  })
})
