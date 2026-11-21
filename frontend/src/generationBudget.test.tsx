import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { estimateGenerationUsd, formatBudgetUsd, GenerationBudgetContext, selectedVideoSeconds } from './generationBudget'
import { GenerationCostBadge } from './shared/components/GenerationCostBadge'
import { DailyBudget } from './shared/components/DailyBudget'
import type { GenerationBudget, Variant } from './types'

const budget: GenerationBudget = { day: '2026-10-16', currency: 'USD', dailyBudgetUsd: 12, budgetUsedUsd: 2.38, unpricedCount: 0, prices: { imagePriceUsd: .04, videoPricePerSecondUsd: .25, depthVideoPricePerSecondUsd: .05, recreateVideoPricePerSecondUsd: .2, cutflowPriceUsd: .15 } }
const render = (child: React.ReactNode, value = budget, failed = false) => renderToStaticMarkup(<GenerationBudgetContext.Provider value={{ budget: value, failed }}>{child}</GenerationBudgetContext.Provider>)

describe('generation cost and personal daily budget', () => {
  it('calculates total images, result seconds and material groups at micro-dollar precision', () => {
    expect(formatBudgetUsd(estimateGenerationUsd(.000001, 3)!)).toBe('$0.000003')
    expect(estimateGenerationUsd(.25, 12 * 4 * 2)).toBe(24)
    expect(render(<GenerationCostBadge category="imagePriceUsd" units={6} />)).toContain('预估 $0.24')
    expect(render(<GenerationCostBadge category="recreateVideoPricePerSecondUsd" units={6} />)).toContain('预估 $1.20')
    expect(render(<GenerationCostBadge category="depthVideoPricePerSecondUsd" units={6} />)).toContain('预估 $0.30')
  })
  it('keeps zero pricing, unknown pricing and unreadable duration distinct', () => {
    expect(estimateGenerationUsd(0, 6)).toBe(0)
    expect(estimateGenerationUsd(null, 6)).toBeNull()
    expect(estimateGenerationUsd(.2, null)).toBeNull()
    expect(estimateGenerationUsd(.2, NaN)).toBeNull()
    expect(render(<GenerationCostBadge category="videoPricePerSecondUsd" units={null} />)).toContain('时长待确认')
    expect(render(<GenerationCostBadge category="imagePriceUsd" units={6} />, { ...budget, prices: { ...budget.prices, imagePriceUsd: null } })).toContain('未定价')
    expect(render(<GenerationCostBadge category="imagePriceUsd" units={6} />, budget, true)).toContain('费用暂不可用')
  })
  it('sums only selected saved video durations and refuses to invent missing seconds', () => {
    const variants = [{ kind: 'STABLE', budgetDurationSeconds: 6 }, { kind: 'DYNAMIC', budgetDurationSeconds: 12 }] as Variant[]
    expect(selectedVideoSeconds(variants, ['DYNAMIC'])).toBe(12)
    expect(selectedVideoSeconds(variants, ['STABLE', 'DYNAMIC'])).toBe(18)
    expect(selectedVideoSeconds([{ kind: 'STABLE' }] as Variant[], ['STABLE'])).toBeNull()
  })
  it('shows known occupancy without presenting unpriced usage as a full total', () => {
    const child = <DailyBudget onOpenUsage={() => {}} />
    expect(render(child)).toContain('$12.00')
    expect(render(child)).toContain('$2.38')
    expect(render(child, { ...budget, dailyBudgetUsd: null })).toContain('不限额')
    expect(render(child, { ...budget, dailyBudgetUsd: 0 })).toContain('今日预算已用完')
    const unknown = render(child, { ...budget, unpricedCount: 2 })
    expect(unknown).toContain('已用（已知）')
    expect(unknown).toContain('2 笔未定价')
  })
})
