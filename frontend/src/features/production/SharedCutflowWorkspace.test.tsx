// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Task, Variant } from '../../types'
import { SharedCutflowWorkspace } from './SharedCutflowWorkspace'

vi.mock('../../api', () => ({
  listCutflowHandoffs: vi.fn(async () => ({ enabled: true, handoffs: [] })),
  listMultiCutflowTasks: vi.fn(async () => ({ tasks: [] })),
  createMultiCutflowTask: vi.fn(),
  retryCutflowHandoff: vi.fn(),
}))
vi.mock('../../shared/components/VideoThumbnail', () => ({
  VideoThumbnail: ({ alt }: { alt: string }) => <span aria-label={alt} />,
}))

const tasks = ['a', 'b'].map(id => ({
  id: 'task-' + id, title: '任务' + id, phase: 'DELIVERABLE', siteProfileId: 'same-brand',
  variants: [{ id: 'variant-' + id, kind: 'STABLE', downloadArtifactId: 'artifact-' + id, generationStatus: 'SUCCEEDED' } as Variant],
  assets: [], settings: { autoSubmitToCutflow: false },
})) as unknown as Task[]
const storageKey = 'cutflow-multi-source-selection:workspace-test'
let root: Root

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  sessionStorage.clear()
  sessionStorage.setItem(storageKey, JSON.stringify(['variant-a', 'variant-b']))
  document.body.innerHTML = '<div id="test-root"></div>'
  root = createRoot(document.getElementById('test-root')!)
})
afterEach(async () => {
  await act(async () => root.unmount())
  vi.unstubAllGlobals()
})

const render = async (visible: boolean, scopeKey = 'records') => {
  await act(async () => root.render(<SharedCutflowWorkspace tasks={tasks} enabled canWrite
    storageKey={storageKey} visible={visible} scopeKey={scopeKey} onSubmitted={() => {}} onPlay={() => {}}>
    {basket => <main><output>{basket.selection.join(',')}</output><button onClick={() => basket.toggle('variant-a')}>切换第一条</button></main>}
  </SharedCutflowWorkspace>))
}
const click = async (selector: string) => {
  const button = document.querySelector<HTMLButtonElement>(selector)
  expect(button).not.toBeNull()
  await act(async () => button!.click())
}

describe('route-scoped shared cut basket', () => {
  it('keeps selection without rendering a portal on unrelated pages', async () => {
    await render(false, 'images')
    expect(document.querySelector('[aria-label="剪辑篮"]')).toBeNull()
    expect(document.querySelector('output')?.textContent).toBe('variant-a,variant-b')
    await render(true)
    expect(document.querySelectorAll('[aria-label="剪辑篮"]')).toHaveLength(1)
    expect(document.querySelector('.cut-basket')).toBeNull()
    expect(document.querySelector('.cut-basket-compact')?.textContent).toContain('已选 2 条原片')
  })

  it('replaces the summary when expanded and collapses after navigation', async () => {
    await render(true)
    await click('[aria-expanded="false"]')
    expect(document.querySelector('.cut-basket')).not.toBeNull()
    expect(document.querySelector('.cut-basket-compact')).toBeNull()
    await render(false, 'images')
    expect(document.querySelector('[aria-label="剪辑篮"]')).toBeNull()
    await render(true, 'make:task-b')
    expect(document.querySelector('.cut-basket')).toBeNull()
    expect(document.querySelector('.cut-basket-compact')).not.toBeNull()
    expect(JSON.parse(sessionStorage.getItem(storageKey)!)).toEqual(['variant-a', 'variant-b'])
  })

  it('supports keyboard ordering, explicit collapse and clearing', async () => {
    await render(true)
    await click('[aria-expanded="false"]')
    const first = document.querySelector('.cut-basket__frame')!
    await act(async () => first.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })))
    expect(document.querySelector('output')?.textContent).toBe('variant-b,variant-a')
    await click('[aria-label="收起送剪篮"]')
    expect(document.querySelector('.cut-basket')).toBeNull()
    await click('[aria-expanded="false"]')
    const clear = Array.from(document.querySelectorAll<HTMLButtonElement>('.cut-basket__actions button')).find(button => button.textContent === '清空')!
    await act(async () => clear.click())
    expect(document.querySelector('[aria-label="剪辑篮"]')).toBeNull()
    expect(JSON.parse(sessionStorage.getItem(storageKey)!)).toEqual([])
  })
})
