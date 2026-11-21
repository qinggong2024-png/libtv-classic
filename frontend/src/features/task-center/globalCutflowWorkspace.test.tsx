import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Task, Variant } from '../../types'
import { SharedCutflowWorkspace } from '../production/SharedCutflowWorkspace'
import { RecordsView } from './RecordsView'
import { ProductionWorkspace } from '../production/ProductionWorkspace'

const noop = () => {}
const refresh = async () => {}
const a = {
  id: 'task-a', title: 'Pagewings 第一条', createdAt: '2026-11-12T03:00:00Z',
  updatedAt: '2026-11-12T03:00:00Z', phase: 'DELIVERABLE',
  variants: [{ id: 'variant-a', kind: 'STABLE', downloadArtifactId: 'artifact-a', generationStatus: 'SUCCEEDED', promptText: '' } as Variant],
  assets: [], assetCount: 1, siteProfileId: 'pagewings',
  settings: { autoSubmitToCutflow: false },
  actionCapabilities: { canRegenerate: false, canCancel: false },
} as unknown as Task
const b = {
  ...a, id: 'task-b', title: 'Pagewings 第二条',
  variants: [{ ...a.variants[0], id: 'variant-b', downloadArtifactId: 'artifact-b' }],
} as Task

afterEach(() => vi.unstubAllGlobals())

describe('single global CUT workspace', () => {
  it('renders only one basket across task list and task detail', () => {
    const storageKey = 'cutflow-multi-source-selection:test-global'
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => key === storageKey ? JSON.stringify(['variant-b']) : null,
      setItem: noop,
    })
    const html = renderToStaticMarkup(<SharedCutflowWorkspace
      tasks={[a, b]} enabled canWrite storageKey={storageKey}
      visible scopeKey="records" compact={false} onSubmitted={refresh} onPlay={noop}
    >{basket => <>
      <RecordsView sharedBasket={basket} tasks={[a, b]} hasMore={false} loadingMore={false}
        lastSyncedAt={null} onLoadMore={noop} onOpen={noop} onPlay={noop}
        onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop} multiCutReady />
      <ProductionWorkspace sharedBasket={basket} task={a} tasks={[a, b]}
        onClose={noop} onChanged={refresh} onRemix={noop} onReuse={noop}
        onPlay={noop} multiCutReady />
    </>}</SharedCutflowWorkspace>)
    expect(html.match(/aria-label="剪辑篮"/g)).toHaveLength(1)
    expect(html).toContain('已选 1 条原片')
    expect(html).toContain('播放第 1 条：Pagewings 第二条')
  })

  it('preserves selection without showing a basket on unrelated pages', () => {
    const storageKey = 'cutflow-multi-source-selection:test-compact'
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => key === storageKey ? JSON.stringify(['variant-a']) : null,
      setItem: noop,
    })
    const html = renderToStaticMarkup(<SharedCutflowWorkspace
      tasks={[a]} enabled canWrite storageKey={storageKey}
      visible={false} scopeKey="images" compact onSubmitted={refresh} onPlay={noop}
    >{basket => <main data-selected={basket.selection.length}>图片中心</main>}</SharedCutflowWorkspace>)
    expect(html).toContain('data-selected="1"')
    expect(html).not.toContain('cut-basket-compact')
    expect(html).not.toContain('aria-label="剪辑篮"')
    expect(html).not.toContain('class="cut-basket"')
  })
})
