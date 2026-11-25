// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Task } from '../../types'
import type { MultiCutBasketController } from '../production/useMultiCutBasket'
import { TaskClipPicker } from './TaskClipPicker'

vi.mock('../../shared/components/VideoThumbnail', () => ({
  VideoThumbnail: ({ alt }: { alt: string }) => <span data-test-cover={alt} />,
}))

const taskWith = (count: number): Task => ({
  id: 'task-picker', title: '真实任务：原片选择',
  variants: Array.from({ length: count }, (_, i) => ({
    id: 'raw-' + (i + 1), downloadArtifactId: 'artifact-' + (i + 1),
  })),
}) as unknown as Task

const onClose = vi.fn()
const onPreview = vi.fn()
const toggle = vi.fn()
const submit = vi.fn()
const basket = {
  enabled: true,
  atCapacity: false,
  isSelected: () => false,
  isInProgress: () => false,
  isPreviouslyCut: () => false,
  toggle,
  submit,
} as unknown as MultiCutBasketController

afterEach(() => { vi.clearAllMocks(); document.body.innerHTML = '' })

describe('独立原片选择层', () => {
  it.each([1, 2, 3, 4])('%i 条原片均保持独立选择，不扩张任务卡片', async count => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    try {
      await act(async () => root.render(<TaskClipPicker task={taskWith(count)} basket={basket}
        onClose={onClose} onPreview={onPreview} />))
      const dialog = document.querySelector<HTMLElement>('[role="dialog"][aria-label^="选择原片："]')
      expect(dialog).not.toBeNull()
      expect(dialog?.className).toContain('allwe-clip-picker--' + count)
      expect(dialog?.querySelectorAll('.allwe-clip-picker__clip')).toHaveLength(count)
      await act(async () => dialog?.querySelector<HTMLButtonElement>('[aria-label="选择原片 1"]')?.click())
      expect(toggle).toHaveBeenCalledWith('raw-1')
      expect(submit).not.toHaveBeenCalled()
      await act(async () => dialog?.querySelector<HTMLButtonElement>('[aria-label="播放原片 1"]')?.click())
      expect(onPreview).toHaveBeenCalledWith('original:raw-1')
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      await act(async () => root.unmount())
      host.remove()
      vi.unstubAllGlobals()
    }
  })
})
