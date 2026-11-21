import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { Task, Variant } from '../../types'
import { RecordsView } from './RecordsView'
import { ProductionWorkspace } from '../production/ProductionWorkspace'

const noop = () => {}
const refresh = async () => {}
const first = {
  id: 'task-one', title: '第一次剪辑任务', createdAt: '2026-11-11T06:00:00Z',
  updatedAt: '2026-11-11T06:10:00Z', phase: 'DELIVERABLE',
  variants: [{
    id: 'variant-one', kind: 'STABLE', generationStatus: 'SUCCEEDED',
    validationStatus: 'PASSED', downloadArtifactId: 'artifact-one', promptText: '',
  } as unknown as Variant],
  assets: [], assetCount: 2, siteProfileId: 'pagewings',
  settings: { durationSeconds: 10, autoSubmitToCutflow: true },
  actionCapabilities: { canRegenerate: false, canCancel: false },
} as unknown as Task
const second = {
  ...first, id: 'task-two', title: '跨任务第二条原片',
  variants: [{
    ...first.variants[0], id: 'variant-two', downloadArtifactId: 'artifact-two',
  } as Variant],
} as Task
const tasks = [first, second]
const storageKey = 'cutflow-multi-source-selection:regression-user'
const renderRecords = () => renderToStaticMarkup(
  <RecordsView tasks={tasks} hasMore={false} loadingMore={false} lastSyncedAt={null}
    onLoadMore={noop} onOpen={noop} onPlay={noop} onRemix={noop} onFeedbackChanged={noop}
    onCreateTask={noop} multiCutReady storageKey={storageKey} />,
)
const renderDetail = (task: Task) => renderToStaticMarkup(
  <ProductionWorkspace task={task} tasks={tasks} basketStorageKey={storageKey}
    onClose={noop} onChanged={refresh} onRemix={noop} onReuse={noop} onPlay={noop}
    multiCutReady />,
)

describe('经典版跨任务重新送剪', () => {
  it('已经生成并曾经自动送剪的任务不再隐藏重新选片入口', () => {
    const html = renderRecords()
    expect(html).toContain('可跨任务合并原片')
    expect(html).toContain('选择原片送剪')
    expect(html.match(/task-center-card__bottom/g)).toHaveLength(2)
  })

  it('列表和不同任务详情使用同一账号剪辑篮并显示跨任务原片', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => key === storageKey ? JSON.stringify(['variant-two']) : null,
      setItem: noop,
    })
    try {
      const records = renderRecords()
      const detailOne = renderDetail(first)
      const detailTwo = renderDetail(second)
      for (const html of [records, detailOne, detailTwo]) {
        expect(html).toContain('已选 1 条原片')
        expect(html).toContain('跨任务第二条原片')
        expect(html).toContain('移除第 1 条原片')
      }
      expect(detailOne).toContain('播放第 1 条：跨任务第二条原片')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
