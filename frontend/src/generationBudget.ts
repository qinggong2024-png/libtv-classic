import { createContext } from 'react'
import type { GenerationBudget, Variant } from './types'

export const GenerationBudgetContext = createContext<{ budget: GenerationBudget | null; failed: boolean }>({ budget: null, failed: false })

export function formatBudgetUsd(amount: number): string {
  return `$${amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`
}

export function estimateGenerationUsd(price: number | null | undefined, units: number | null): number | null {
  if (price == null || units == null || !Number.isFinite(price) || !Number.isFinite(units) || price < 0 || units < 0) return null
  return Math.round(price * 1_000_000) * units / 1_000_000
}

export function selectedVideoSeconds(variants: Variant[], kinds: Variant['kind'][]): number | null {
  const selected = variants.filter((variant) => kinds.includes(variant.kind))
  if (selected.some((variant) => !variant.budgetDurationSeconds || !Number.isFinite(variant.budgetDurationSeconds))) return null
  return selected.reduce((total, variant) => total + (variant.budgetDurationSeconds ?? 0), 0)
}
