import type { MultiCutflowTask } from '../api'
import type { Task } from '../types'
import { parseTaskTimestamp } from '../taskTiming'
import { RESULT_PHASES } from './workflowStates'

export const AUTO_CUTFLOW_WAIT_MS = 180_000

export interface AutoCutflowCandidate {
  taskId: string
  readyAt: number | null
}

/** Only fully downloaded and validated originals can enter automatic admission. */
export function autoCutflowCandidate(task: Task): AutoCutflowCandidate | null {
  if (task.settings?.autoSubmitToCutflow !== true || !RESULT_PHASES.has(task.phase)
    || !task.variants.length || !task.variants.every(variant => (
      Boolean(variant.downloadArtifactId)
      && ['SUCCEEDED', 'PARTIAL'].includes(variant.generationStatus)
      && variant.downloadStatus === 'READY'
      && ['PASSED', 'WARNING'].includes(variant.validationStatus)
    ))) return null
  // The persisted parent update is a conservative lower bound for elapsed
  // readiness, never task creation time or the time this browser was opened.
  const readyAt = parseTaskTimestamp(task.updatedAt ?? '')
  return { taskId: task.id, readyAt: Number.isFinite(readyAt) ? readyAt : null }
}

/** A manual salvage must share the auto worker's stable key and clip order. */
export function partialAutoCutflowKey(task: Task, sourceIds: string[]): string | null {
  if (task.settings?.autoSubmitToCutflow !== true
    || !['PARTIALLY_DELIVERABLE', 'ACTION_REQUIRED', 'DELIVERABLE'].includes(task.phase)
    || !task.variants.length || !sourceIds.length) return null
  const ready = task.variants.filter(variant => Boolean(variant.downloadArtifactId)
    && ['SUCCEEDED', 'PARTIAL'].includes(variant.generationStatus)
    && variant.downloadStatus === 'READY'
    && ['PASSED', 'WARNING'].includes(variant.validationStatus))
  const excluded = task.variants.filter(variant => !ready.includes(variant))
  if (!excluded.length || excluded.some(variant => !(
    variant.generationStatus === 'FAILED'
    || (['SUCCEEDED', 'PARTIAL'].includes(variant.generationStatus)
      && (variant.validationStatus === 'FAILED' || variant.downloadStatus === 'FAILED'))
  ))) return null
  if (sourceIds.length !== ready.length
    || sourceIds.some((id, index) => id !== ready[index].id)) return null
  return `auto-unified-v2:${task.id}`
}

export function autoCutflowWaitDelayed(task: Task, now: number): boolean {
  const candidate = autoCutflowCandidate(task)
  return candidate?.readyAt != null && now - candidate.readyAt >= AUTO_CUTFLOW_WAIT_MS
}

/** Query faster only during the short handoff window; never submit or retry. */
export function autoCutflowPollDelay(
  candidates: AutoCutflowCandidate[], batches: MultiCutflowTask[],
  now: number, active: boolean, readFailed = false,
): number {
  if (readFailed) return 30_000
  const covered = new Set(batches.flatMap(batch => batch.sources.map(source => source.taskId)))
  if (candidates.some(candidate => !covered.has(candidate.taskId)
    && candidate.readyAt != null && now - candidate.readyAt >= 0
    && now - candidate.readyAt < AUTO_CUTFLOW_WAIT_MS)) return 5_000
  return active ? 15_000 : 30_000
}
