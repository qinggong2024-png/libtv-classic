// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it, vi } from 'vitest'
import type { Task } from '../../types'
import type { MultiCutBasketController } from '../production/useMultiCutBasket'
import { RecordsView } from './RecordsView'

const noop = () => {}
const task = {
  id: 'task-ui', clientTaskId: 'PW-001', title: 'PW · 真实任务',
  createdAt: '2026-11-23T09:00:00Z', updatedAt: '2026-11-23T10:00:00Z',
  phase: 'DELIVERABLE', generationProvider: 'KLING', workflowKind: 'FIXED_TEMPLATE',
  assets: [], assetCount: 1, variants: [{ id: 'clip-a', kind: 'STABLE', downloadArtifactId: 'clip-artifact', generationStatus: 'SUCCEEDED', validationStatus: 'PASSED' }],
} as unknown as Task
const basket = {
  enabled: false, multiTasks: [], multiTasksLoaded: true, multiTaskError: '', multiTasksCheckedAt: Date.now(),
  relatedMultiSourceTasksForTask: () => [], singleSourceVersionsFor: () => [],
} as unknown as MultiCutBasketController

describe('任务中心双点击区与极简播放器', () => {
  it('首次进入默认视频卡片，手动切换列表后保留当前会话选择', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    sessionStorage.removeItem('allwe-task-center-view')
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    try {
      await act(async () => root.render(<RecordsView sharedBasket={basket} tasks={[task]}
        hasMore={false} loadingMore={false} lastSyncedAt={null}
        onLoadMore={noop} onOpen={noop} onPlay={noop} onRemix={noop}
        onFeedbackChanged={noop} onCreateTask={noop} />))
      expect(host.querySelector('.task-center-list.is-grid')).not.toBeNull()
      expect(host.querySelector('button[aria-label="视频卡片"]')?.getAttribute('aria-pressed')).toBe('true')
      await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="紧凑列表"]')?.click())
      expect(host.querySelector('.task-center-list.is-grid')).toBeNull()
      expect(host.querySelector('button[aria-label="紧凑列表"]')?.getAttribute('aria-pressed')).toBe('true')
      expect(sessionStorage.getItem('allwe-task-center-view')).toBe('list')
    } finally {
      await act(async () => root.unmount())
      host.remove()
      sessionStorage.removeItem('allwe-task-center-view')
      vi.unstubAllGlobals()
    }
  })

  it('部分原片在生成时显示模糊状态，刷新后解除，预览仍可打开', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    sessionStorage.removeItem('allwe-task-center-view')
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const partial = { ...task, phase: 'GENERATING', variants: [task.variants[0], { ...task.variants[0], id: 'clip-b', downloadArtifactId: null, generationStatus: 'RUNNING' }] } as Task
    const render = (item: Task) => root.render(<RecordsView sharedBasket={basket} tasks={[item]}
      hasMore={false} loadingMore={false} lastSyncedAt={null}
      onLoadMore={noop} onOpen={noop} onPlay={noop} onRemix={noop}
      onFeedbackChanged={noop} onCreateTask={noop} />)
    try {
      await act(async () => render(partial))
      expect(host.querySelector('.allwe-media-cover.is-cut-busy .allwe-media-cover__cut-status')?.textContent).toContain('正在生成原片 1/2')
      await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label^="播放原片"]')?.click())
      expect(document.body.querySelector('[role="dialog"][aria-label="视频预览"]')).not.toBeNull()
      await act(async () => document.body.querySelector<HTMLButtonElement>('button[aria-label="关闭预览"]')?.click())
      await act(async () => render({ ...partial, phase: 'PARTIALLY_DELIVERABLE' }))
      expect(host.querySelector('.allwe-media-cover.is-cut-busy')).toBeNull()
    } finally {
      await act(async () => root.unmount())
      host.remove()
      sessionStorage.removeItem('allwe-task-center-view')
      vi.unstubAllGlobals()
    }
  })

  it('卡片模式宽度由 class 控制；点击封面预览，点击信息进入详情', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const onOpen = vi.fn()
    try {
      await act(async () => { root.render(<RecordsView sharedBasket={basket} tasks={[task]} hasMore={false}
        loadingMore={false} lastSyncedAt={null} onLoadMore={noop} onOpen={onOpen}
        onPlay={noop} onRemix={noop} onFeedbackChanged={noop} onCreateTask={noop} />) })
      await act(async () => {
        host.querySelector<HTMLButtonElement>('button[aria-label="视频卡片"]')?.click()
      })
      expect(host.querySelector('.task-center-list.is-grid')).not.toBeNull()
      await act(async () => {
        host.querySelector<HTMLButtonElement>('button[aria-label^="播放原片"]')?.click()
      })
      expect(document.body.querySelector('[role="dialog"][aria-label="视频预览"]')).not.toBeNull()
      expect(onOpen).not.toHaveBeenCalled()
      expect(document.body.textContent).toContain('下载原片')
      await act(async () => {
        document.body.querySelector<HTMLButtonElement>('button[aria-label="关闭预览"]')?.click()
      })
      await act(async () => {
        host.querySelector<HTMLButtonElement>('button[aria-label^="任务详情："]')?.click()
      })
      expect(onOpen).toHaveBeenCalledTimes(1)
    } finally {
      await act(async () => root.unmount())
      host.remove()
      vi.unstubAllGlobals()
      sessionStorage.removeItem('allwe-task-center-view')
    }
  })
})
