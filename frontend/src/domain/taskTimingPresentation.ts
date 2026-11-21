import type { Task } from '../types'
import { parseTaskTimestamp } from '../taskTiming'
import { SETTLED_PHASES } from './workflowStates'

export type TaskTimingStage = NonNullable<Task['timing']>['stages'][number]

export const STOPPED_TIMING_STATUSES = new Set(['FAILED', 'INTERRUPTED', 'UNKNOWN', 'CANCELLED'])

export function stageElapsedSeconds(stage: TaskTimingStage, now: number): number | null {
  if (stage.status !== 'ACTIVE' || !stage.activeStartedAt) return stage.durationSeconds
  const startedAt = parseTaskTimestamp(stage.activeStartedAt)
  if (!Number.isFinite(startedAt)) return stage.durationSeconds
  const live = Math.max(0, (now - startedAt) / 1000)
  return stage.key === 'ANALYSIS' ? (stage.durationSeconds ?? 0) + live : live
}

export function taskTimingStages(task: Task): TaskTimingStage[] {
  return (task.timing?.stages ?? []).map((stage) => {
    // Also handle snapshots from an API process that predates stopped-stage
    // support. A settled parent must never leave a client timer running.
    const unknown = stage.key === 'GENERATION' && task.variants.some((variant) => variant.generationStatus === 'UNKNOWN')
    const failed = stage.key === 'GENERATION' && task.variants.some((variant) => variant.generationStatus === 'FAILED')
    const invalidCompletion = stage.status === 'COMPLETED' && (unknown || failed)
    if (STOPPED_TIMING_STATUSES.has(stage.status)) {
      return { ...stage, stoppedAt: stage.stoppedAt ?? task.updatedAt, activeStartedAt: null }
    }
    if ((!invalidCompletion && stage.status !== 'ACTIVE') || !SETTLED_PHASES.has(task.phase)) return stage
    const stoppedAt = stage.stoppedAt ?? task.updatedAt
    const end = parseTaskTimestamp(stoppedAt)
    const start = stage.activeStartedAt ? parseTaskTimestamp(stage.activeStartedAt) : NaN
    const status = task.phase === 'CANCELLED' ? 'CANCELLED'
      : unknown ? 'UNKNOWN' : failed ? 'FAILED' : 'INTERRUPTED'
    return {
      ...stage,
      status,
      durationSeconds: Number.isFinite(start) && Number.isFinite(end) && end >= start
        ? stageElapsedSeconds(stage, end)
        : stage.durationSeconds,
      activeStartedAt: null,
      stoppedAt,
    }
  })
}

export function taskTimingStoppedAt(task: Task): string | null {
  const times = taskTimingStages(task)
    .filter((stage) => STOPPED_TIMING_STATUSES.has(stage.status) && stage.stoppedAt)
    .map((stage) => stage.stoppedAt!)
    .filter((time) => Number.isFinite(parseTaskTimestamp(time)))
  return times.sort((a, b) => parseTaskTimestamp(b) - parseTaskTimestamp(a))[0] ?? null
}
