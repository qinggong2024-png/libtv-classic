// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it, vi } from 'vitest'
import { RecordsView } from './RecordsView'
import type { MultiCutBasketController } from '../production/useMultiCutBasket'

const noop = () => {}

describe('任务中心手动刷新', () => {
  it('点击刷新会查询任务与剪辑状态，防止重复点击并保留当前搜索条件', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    let release: () => void = noop
    const onRefresh = vi.fn(() => new Promise<void>(resolve => { release = resolve }))
    const refreshMultiTasks = vi.fn()
    const sharedBasket = {
      enabled: true,
      multiTasks: [],
      multiTasksLoaded: true,
      multiTaskError: '',
      multiTasksCheckedAt: Date.now(),
      refreshMultiTasks,
    } as unknown as MultiCutBasketController
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    try {
      await act(async () => {
        root.render(<RecordsView sharedBasket={sharedBasket} tasks={[]} hasMore={false}
          loadingMore={false} lastSyncedAt={null} onLoadMore={noop} onRefresh={onRefresh}
          onOpen={noop} onPlay={noop} onRemix={noop} onFeedbackChanged={noop}
          onCreateTask={noop} initialQuery="PW" />)
      })
      const button = container.querySelector<HTMLButtonElement>('button[aria-label="刷新任务状态"]')
      expect(button).not.toBeNull()
      await act(async () => { button?.click() })
      expect(onRefresh).toHaveBeenCalledTimes(1)
      expect(refreshMultiTasks).toHaveBeenCalledTimes(1)
      expect(button?.disabled).toBe(true)
      expect(container.querySelector('button[aria-label="正在刷新任务状态"]')).not.toBeNull()
      await act(async () => { release() })
      const cooldownButton = container.querySelector<HTMLButtonElement>('button[aria-label="刷新冷却中，请稍后重试"]')
      expect(cooldownButton?.disabled).toBe(true)
      cooldownButton?.click()
      expect(onRefresh).toHaveBeenCalledTimes(1)
      await act(async () => { await vi.advanceTimersByTimeAsync(4999) })
      expect(container.querySelector<HTMLButtonElement>('button[aria-label="刷新冷却中，请稍后重试"]')?.disabled).toBe(true)
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      expect(container.querySelector<HTMLButtonElement>('button[aria-label="刷新任务状态"]')?.disabled).toBe(false)
      expect(container.querySelector<HTMLInputElement>('input[aria-label="搜索任务名称、编号或品牌"]')?.value).toBe('PW')
      await act(async () => { button?.click() })
      expect(onRefresh).toHaveBeenCalledTimes(2)
      expect(refreshMultiTasks).toHaveBeenCalledTimes(2)
      await act(async () => { release() })
    } finally {
      await act(async () => { root.unmount() })
      container.remove()
      vi.unstubAllGlobals()
      vi.useRealTimers()
    }
  })
})
