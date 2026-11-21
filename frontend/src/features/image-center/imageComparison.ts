import type { ImageGenerationOutput } from '../../imageCenterModel'

// Resolve stale history/selection IDs without mutating the video selection.
export function imageComparisonIds(outputs: ImageGenerationOutput[], preferredIds: readonly string[]): string[] {
  const available = new Set(outputs.map((output) => output.id))
  return [...new Set([...preferredIds.filter((id) => available.has(id)), ...available])].slice(0, 2)
}

export function changeImageComparison(ids: readonly string[], side: number, outputId: string): string[] {
  const next = [...ids]
  const otherSide = side === 0 ? 1 : 0
  if (next[otherSide] === outputId) next[otherSide] = next[side]
  next[side] = outputId
  return next
}
