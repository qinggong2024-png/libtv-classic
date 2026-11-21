import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { GenerationBudgetContext } from '../../generationBudget'
import type { GenerationBudget } from '../../types'
import { GenerationCostBadge } from './GenerationCostBadge'

describe('depth-first generation budget', () => {
  const render = (depth: number | null, replacement: number | null) => renderToStaticMarkup(
    <GenerationBudgetContext.Provider value={{ budget: { prices: {
      depthVideoPricePerSecondUsd: depth, recreateVideoPricePerSecondUsd: replacement,
    } } as GenerationBudget, failed: false }}>
      <GenerationCostBadge category="recreateVideoPricePerSecondUsd" additionalCategory="depthVideoPricePerSecondUsd" units={6} />
    </GenerationBudgetContext.Provider>,
  )

  it('includes both paid stages in the preview', () => {
    expect(render(0.05, 0.2)).toContain('预估 $1.50')
  })

  it('does not understate the price when either stage is unpriced', () => {
    expect(render(null, 0.2)).toContain('未定价')
    expect(render(0.05, null)).toContain('未定价')
    expect(render(0, 0)).toContain('预估 $0.00')
  })
})
