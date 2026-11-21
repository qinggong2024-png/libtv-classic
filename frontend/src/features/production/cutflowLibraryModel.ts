import type { CutflowHandoff } from '../../api'
import type { Task, Variant } from '../../types'

export const MAX_CUTFLOW_SOURCES = 8

export type CutflowLibraryFilter = 'cutflow-ready' | 'cutflow-editing' | 'cutflow-finished'
export type CutflowSource = { task: Task; variant: Variant; artifactId: string }
export type CutflowSourceState = { enabled: boolean; handoffs: CutflowHandoff[]; error?: string }

export function moveCutflowSource(ids: string[], sourceId: string, direction: -1 | 1): string[] {
  const from = ids.indexOf(sourceId)
  const to = from + direction
  if (from < 0 || to < 0 || to >= ids.length) return ids
  const next = [...ids]
  ;[next[from], next[to]] = [next[to], next[from]]
  return next
}

export function reconcileCutflowSelection(ids: string[], validIds: Set<string>): string[] {
  return ids.filter(id => validIds.has(id))
}

export function toggleCutflowSelection(
  ids: string[],
  sourceId: string,
  maxSources = MAX_CUTFLOW_SOURCES,
): string[] {
  if (ids.includes(sourceId)) return ids.filter(id => id !== sourceId)
  return ids.length < maxSources ? [...ids, sourceId] : ids
}

export function cutflowBrandConflict(sources: CutflowSource[]): boolean {
  const brands = new Set(sources.flatMap(source => (
    source.task.siteProfileId ? [source.task.siteProfileId] : []
  )))
  return brands.size > 1
}

export function cutflowUnverifiedBrandCount(sources: CutflowSource[]): number {
  return sources.filter(source => !source.task.siteProfileId).length
}

export function cutflowSourceCanJoinBasket(
  state: CutflowSourceState | undefined,
  artifactId: string,
): boolean {
  if (!state?.enabled || state.error) return false
  const current = state.handoffs.find(item => item.sourceArtifactId === artifactId)
  if (!current) return true
  return current.outputs.length > 0 || current.status === 'FAILED' || current.status === 'CANCELED'
}

export function cutflowSources(tasks: Task[]): CutflowSource[] {
  return tasks.flatMap(task => task.variants.flatMap(variant => variant.downloadArtifactId
    ? [{ task, variant, artifactId: variant.downloadArtifactId }] : []))
    .sort((a, b) => Date.parse(b.task.updatedAt) - Date.parse(a.task.updatedAt))
}

export function cutflowSourceSummary(source: CutflowSource, state?: CutflowSourceState) {
  const matching = state?.handoffs.filter(item => item.sourceArtifactId === source.artifactId) ?? []
  const current = matching[0]
  const outputs = matching.flatMap(item => item.outputs)
  // Unknown/disabled reads must never look like a video that has not been sent.
  const filter: CutflowLibraryFilter | null = !state?.enabled ? null
    : current ? (outputs.length ? 'cutflow-finished' : 'cutflow-editing')
      : state.error ? null : 'cutflow-ready'
  return { current, outputs, filter }
}
