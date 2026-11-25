// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Task } from '../../types'
import type { MultiCutBasketController } from '../production/useMultiCutBasket'
import { TaskClipPickerLauncher } from './TaskClipPickerLauncher'

vi.mock('../../shared/components/VideoThumbnail', () => ({
  VideoThumbnail: ({ alt }: { alt: string }) => <span data-poster={alt} />,
}))
vi.mock('../../shared/hooks/useMediaObjectUrl', () => ({
  useMediaObjectUrl: () => ({ url: 'blob:testing-clip', error: '' }),
}))

const task = {
  id: 'task-clip', title: 'Pagewings · 商品视频',
  variants: [{ id: 'v1', downloadArtifactId: 'artifact-v1' }],
} as unknown as Task
const selected = new Set<string>()
const basket = {
  enabled: true, multiTasks: [], multiTasksLoaded: true, multiTaskError: '',
  multiTasksCheckedAt: Date.now(),
  relatedMultiSourceTasksForTask: () => [], singleSourceVersionsFor: () => [],
  isSelected: (id: string) => selected.has(id),
  isInProgress: () => false, isPreviouslyCut: () => false, atCapacity: false,
  toggle: (id: string) => selected.has(id) ? selected.delete(id) : selected.add(id),
  refreshMultiTasks: vi.fn(),
} as unknown as MultiCutBasketController

afterEach(() => { document.body.innerHTML = ''; selected.clear(); vi.clearAllMocks() })

describe('选片内播放的二级弹窗生命周期', () => {
  it('播放器关闭后仍留在选片窗口，保留原片选择；Escape 只关闭最上层', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    try {
      await act(async () => root.render(<TaskClipPickerLauncher task={task} basket={basket} />))
      const launcher = host.querySelector<HTMLButtonElement>('[aria-label^="选择原片送剪："]')
      await act(async () => launcher?.click())
      expect(document.querySelector('[aria-label^="选择原片："]')).not.toBeNull()
      await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="选择原片 1"]')?.click())
      expect(selected.has('v1')).toBe(true)
      await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="播放原片 1"]')?.click())
      expect(document.querySelector('[role="dialog"][aria-label="视频预览"]')).not.toBeNull()
      expect(document.querySelector('[role="dialog"][aria-label^="选择原片："]')).not.toBeNull()
      await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="关闭预览"]')?.click())
      expect(document.querySelector('[role="dialog"][aria-label="视频预览"]')).toBeNull()
      expect(document.querySelector('[role="dialog"][aria-label^="选择原片："]')).not.toBeNull()
      expect(selected.has('v1')).toBe(true)
      await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="播放原片 1"]')?.click())
      await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
      expect(document.querySelector('[role="dialog"][aria-label="视频预览"]')).toBeNull()
      expect(document.querySelector('[role="dialog"][aria-label^="选择原片："]')).not.toBeNull()
      await act(async () => document.querySelector<HTMLButtonElement>('.allwe-clip-picker__done')?.click())
      expect(document.querySelector('[role="dialog"][aria-label^="选择原片："]')).toBeNull()
      expect(selected.has('v1')).toBe(true)
    } finally {
      await act(async () => root.unmount())
      host.remove()
      vi.unstubAllGlobals()
    }
  })
})
