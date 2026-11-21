import { parseTaskTimestamp } from '../taskTiming'

export type TimeWindow = {
  startedAt?: string | null
  endedAt?: string | null
  active?: boolean
}

/**
 * End-to-end elapsed time and observed intervals. A provider's internal queue
 * may be included within a generation interval: these are stage-occupancy
 * times, not independently verified cloud compute times.
 */
export function timeBreakdown(
  startAt: string,
  endAt: string | number,
  processing: TimeWindow[],
  now: number,
): { totalSeconds: number; processingSeconds: number; gapSeconds: number } | null {
  const start = parseTaskTimestamp(startAt)
  const end = typeof endAt === 'number' ? endAt : parseTaskTimestamp(endAt)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null
  const intervals = processing.flatMap(({ startedAt, endedAt, active }) => {
    if (!startedAt) return []
    const begin = parseTaskTimestamp(startedAt)
    const finish = active ? now : endedAt ? parseTaskTimestamp(endedAt) : NaN
    if (!Number.isFinite(begin) || !Number.isFinite(finish) || finish < begin) return []
    const left = Math.max(start, begin)
    const right = Math.min(end, finish)
    return right > left ? [{ left, right }] : []
  }).sort((a, b) => a.left - b.left)
  let busyMs = 0
  let boundary = start
  for (const interval of intervals) {
    busyMs += Math.max(0, interval.right - Math.max(boundary, interval.left))
    boundary = Math.max(boundary, interval.right)
  }
  const totalSeconds = Math.floor((end - start) / 1000)
  const processingSeconds = Math.min(totalSeconds, Math.floor(busyMs / 1000))
  return { totalSeconds, processingSeconds, gapSeconds: totalSeconds - processingSeconds }
}

export function timeBeforeStage(startAt: string, previousEnd: string | null, stageStart: string | null | undefined): number | null {
  if (!stageStart) return null
  const from = parseTaskTimestamp(previousEnd ?? startAt)
  const to = parseTaskTimestamp(stageStart)
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null
  return Math.max(0, Math.floor((to - from) / 1000))
}
