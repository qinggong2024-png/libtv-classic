// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Task } from '../../types'
import type { MultiCutBasketController } from '../production/useMultiCutBasket'
import { RecordsView } from './RecordsView'

const { thumbnailRenders } = vi.hoisted(() => ({
  thumbnailRenders: new Map<string, number>(),
}))
vi.mock('../../shared/components/VideoThumbnail', () => ({
  VideoThumbnail: ({ artifactId }: { artifactId: string }) => {
    thumbnailRenders.set(artifactId, (thumbnailRenders.get(artifactId) ?? 0) + 1)
    return <span data-thumbnail={artifactId} />
  },
}))
const noop = () => {}
const tasks = Array.from({ length: 6 }, (_, index) => ({
  id: 'task-' + index,
  clientTaskId: 'PW-' + index,
  title: '任务 ' + index,
  phase: 'DELIVERABLE',
  createdAt: '2026-11-24T09:00:00Z',
  updatedAt: '2026-11-24T09:00:00Z',
  assets: [],
  assetCount: 1,
  variants: [{
    id: 'variant-' + index, downloadArtifactId: 'artifact-' + index,
    generationStatus: 'SUCCEEDED', downloadStatus: 'READY', validationStatus: 'PASSED',
  }],
})) as unknown as Task[]
const basket = {
  enabled: true, multiTasks: [], multiTasksLoaded: true, multiTaskError: '',
  multiTasksCheckedAt: Date.now(), relatedMultiSourceTasksForTask: () => [],
  singleSourceVersionsFor: () => [],
  isSelected: () => false, isInProgress: () => false,
  isPreviouslyCut: () => false, atCapacity: false, toggle: noop,
} as unknown as MultiCutBasketController

afterEach(() => { thumbnailRenders.clear(); sessionStorage.removeItem('allwe-task-center-view') })

describe('选择原片时只更新目标卡片', () => {
  it('不重绘其他任务封面，不刷新任务或改变任务卡片节点', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    try {
      await act(async () => root.render(<RecordsView tasks={tasks} sharedBasket={basket} hasMore={false}
        loadingMore={false} lastSyncedAt={null} onLoadMore={noop} onRefresh={onRefresh}
        onOpen={noop} onPlay={noop} onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop} />))
      const before = [...host.querySelectorAll('.task-center-card')]
      expect(before).toHaveLength(6)
      const unaffectedImageRenders = thumbnailRenders.get('artifact-5')
      await act(async () => before[0].querySelector<HTMLButtonElement>('.task-center-card__pick')?.click())
      expect(document.body.querySelector('.allwe-clip-picker')).not.toBeNull()
      const after = [...host.querySelectorAll('.task-center-card')]
      expect(after.length).toBe(6)
      expect(after.every((node, index) => node === before[index])).toBe(true)
      expect(thumbnailRenders.get('artifact-5')).toBe(unaffectedImageRenders)
      expect(onRefresh).not.toHaveBeenCalled()
    } finally {
      await act(async () => root.unmount())
      host.remove()
      vi.unstubAllGlobals()
    }
  })
})
