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
