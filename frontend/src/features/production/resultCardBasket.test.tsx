// 剪辑篮在任务页原片卡片上的入口：用静态渲染断言按钮确实产出，
// 阈值是「其他页面不传 onToggleBasket 时零影响」。
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Task, Variant } from '../../types'
import { ResultCard } from './ResultCard'

const task = {
  id: 'task-1', title: '套装 01-分镜-1', createdAt: '2026-10-28T08:00:00Z',
  updatedAt: '2026-10-28T09:00:00Z', phase: 'DELIVERABLE', assets: [], variants: [],
  siteProfileId: 'tokyotiger', settings: {},
} as unknown as Task

const variant = {
  id: 'variant-1', downloadArtifactId: 'artifact-1',
  generationStatus: 'SUCCEEDED', validationStatus: 'PASSED',
} as unknown as Variant

const noop = () => {}

function render(extra: Record<string, unknown> = {}) {
  return renderToStaticMarkup(
    <ResultCard
      task={task}
      variant={variant}
      onPlay={noop}
      onRemix={noop}
      onOpen={noop}
      onFeedbackChanged={noop}
      {...extra}
    />,
  )
}

describe('多源剪辑篮的卡片入口', () => {
  it('传入 onToggleBasket 时把底部入口改成「送入剪辑」', () => {
    const html = render({ onToggleBasket: noop })
    expect(html).toContain('cutflow-source-toggle')
    expect(html).toContain('type="checkbox"')
    expect(html).toContain('选择送剪')
  })

  it('已加入时给出「已加入剪辑篮」状态，并标记卡片已选', () => {
    const html = render({ onToggleBasket: noop, basketSelected: true })
    expect(html).toContain('已选择送剪')
    expect(html).toContain('cutflow-source-toggle is-selected')
    expect(html).toContain('checked=""')
    expect(html).toContain('is-basket-selected')
  })

  it('已送剪辑的原片保留明确但不可操作的入口', () => {
    const html = render({ onToggleBasket: noop, basketDisabled: true, basketDisabledLabel: '剪辑中' })
    expect(html).toContain('剪辑中')
    expect(html).toContain('disabled=""')
    expect(html).toContain('当前剪辑尚未结束')
  })

  it('已有成片时提供重新剪辑入口', () => {
    const html = render({ onToggleBasket: noop, basketActionLabel: '重新剪辑' })
    expect(html).toContain('重新剪辑')
    expect(html).toContain('type="checkbox"')
  })

  it('不传 onToggleBasket 时回退到原有「剪辑与成片」入口，保证其他页面零影响', () => {
    const html = render({ onEditing: noop })
    expect(html).toContain('剪辑与成片')
    expect(html).not.toContain('选择送剪')
    expect(html).not.toContain('is-basket-selected')
  })
})
