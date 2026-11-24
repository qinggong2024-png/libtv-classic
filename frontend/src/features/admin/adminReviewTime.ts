import type { AdminVideoTask, AdminVideoTrace } from '../../types'

// Historical Classic API values without an offset are stored as UTC.
export function reviewDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const text = value.trim().replace(' ', 'T')
  const normalized = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(text) ? text + 'Z' : text
  if (!/T.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized)) return null
  const date = new Date(normalized)
  return Number.isFinite(date.getTime()) ? date : null
}

export function taskCreationTime(task: AdminVideoTask, traces: AdminVideoTrace[] = []): string | null {
  // Never fall back to legacy gallery createdAt (it was the newest raw artifact).
  const candidates = [
    ...traces.filter(t => t.taskId === task.taskId).map(t => t.taskCreatedAt),
    ...task.clips.map(c => c.taskCreatedAt),
    ...(task.latestRawAt !== undefined ? [task.createdAt] : []),
  ]
  return candidates.find(value => reviewDate(value) !== null) ?? null
}
