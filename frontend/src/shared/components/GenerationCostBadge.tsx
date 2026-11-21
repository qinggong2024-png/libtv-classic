import { useContext } from 'react'
import { estimateGenerationUsd, formatBudgetUsd, GenerationBudgetContext } from '../../generationBudget'
import type { GenerationBudget } from '../../types'

export function GenerationCostBadge({ category, additionalCategory, units }: { category: keyof GenerationBudget['prices']; additionalCategory?: keyof GenerationBudget['prices']; units: number | null }) {
  const { budget, failed } = useContext(GenerationBudgetContext)
  const primary = budget?.prices[category]
  const additional = additionalCategory ? budget?.prices[additionalCategory] : 0
  const rate = primary == null || additional == null ? null : primary + additional
  const amount = estimateGenerationUsd(failed ? null : rate, units)
  const label = failed ? '费用暂不可用' : !budget ? '费用读取中' : units === null ? '时长待确认' : amount === null ? '未定价' : `预估 ${formatBudgetUsd(amount)}`
  return <span className="generation-cost-badge" title="按全站单价估算的本次预算占用（USD），以供应商账单为准">{label}</span>
}
