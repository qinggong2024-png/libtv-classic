// Use the time-bearing fields shared by single-source and multi-source CUT results.

export type CutflowTimingSource = { createdAt?: string; updatedAt?: string; outputs: Array<{ createdAt: string }> }
export type CutflowTimingStatus = 'ACTIVE' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'UNKNOWN' | 'PENDING'

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value.replace(' ', 'T')}Z`
  const parsed = Date.parse(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

/** Handoff-to-output elapsed time, including delivery/queue time; not pure CUT rendering time. */
export function cutflowElapsedSeconds(
  source: CutflowTimingSource | null | undefined,
  status: CutflowTimingStatus,
  now: number,
): number | null {
  if (!source || status === 'PENDING' || status === 'UNKNOWN') return null
  const start = timestamp(source.createdAt)
  if (start === null) return null
  let end: number | null = null
  if (status === 'ACTIVE') end = now
  else if (status === 'COMPLETED') {
    const finished = source.outputs.map((output) => timestamp(output.createdAt)).filter((value): value is number => value !== null)
    end = finished.length ? Math.max(...finished) : null
  } else if (status === 'FAILED' || status === 'CANCELLED') end = timestamp(source.updatedAt)
  if (end === null || end < start) return null
  return Math.max(0, Math.floor((end - start) / 1000))
}
