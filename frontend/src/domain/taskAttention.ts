import type { MultiCutflowTask } from '../api'
import type { Task } from '../types'
import { AWAITING_USER_INPUT_PHASES, FAILED_PHASES } from './workflowStates'
import { taskCenterState } from './taskCenterState'

export type TaskAttentionAcknowledgement = { taskId: string; signature: string }
export type TaskAttentionMap = Record<string, string>
export type CutflowLoadState = { loaded?: boolean; error?: boolean; now?: number }

/** Single classifier shared by the task tab, navigation badge and homepage. */
export function taskAttentionGroup(task: Task, batches: MultiCutflowTask[], state: CutflowLoadState = {}) {
  if (task.phase === 'CANCELLED') return 'cancelled'
  if (task.phase === 'PARTIALLY_DELIVERABLE') return 'done'
  if (FAILED_PHASES.has(task.phase)) return 'attention'
  return taskCenterState(task, batches, state).group
}

/**
 * Stable incident fingerprint: changes only when underlying task/CUT evidence changes,
 * not when a user opens a page, a timer ticks or the task's updatedAt is refreshed.
 * This is presentation state only: never alters generation, billing, or CUT processing.
 */
export function taskAttentionSignature(task: Task, batches: MultiCutflowTask[]) {
  const generationIssue = AWAITING_USER_INPUT_PHASES.has(task.phase)
    || task.variants.some(variant => variant.generationStatus === 'UNKNOWN')
  const evidence = JSON.stringify(generationIssue
    ? [task.phase, task.phaseReasonCode ?? '',
        task.variants.map(v => [v.id, v.generationStatus, v.errorCode ?? ''])
          .sort((a, b) => String(a[0]).localeCompare(String(b[0])))]
    : [task.phase,
        batches.map(b => [b.id, b.status, b.errorCode ?? '', b.outputs.length])
          .sort((a, b) => String(a[0]).localeCompare(String(b[0])))])
  let hash = 2166136261
  for (let index = 0; index < evidence.length; index++) {
    hash ^= evidence.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return 'v1-' + (hash >>> 0).toString(16).padStart(8, '0')
}

export function isTaskAttentionAcknowledged(taskId: string, signature: string, acknowledgements: TaskAttentionMap) {
  return acknowledgements[taskId] === signature
}
