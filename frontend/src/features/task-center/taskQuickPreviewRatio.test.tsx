// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it, vi } from 'vitest'
import { TaskQuickPreview } from './TaskQuickPreview'
import type { PlayableMedia } from './taskCenterMedia'

vi.mock('../../shared/hooks/useMediaObjectUrl', () => ({
  useMediaObjectUrl: (source: string) => ({ url: 'blob:' + source, error: '' }),
}))

describe('任务中心播放器真实比例', () => {
  it('读取视频元数据、按当前片段比例布局，切换片段不继承旧比例', async () => {
    const media = [
      { kind: 'original', key: 'portrait', label: '原片 1', artifactId: 'portrait', variant: {} },
      { kind: 'original', key: 'landscape', label: '原片 2', artifactId: 'landscape', variant: {} },
    ] as PlayableMedia[]
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    try {
      await act(async () => root.render(<TaskQuickPreview media={media} startKey="portrait" onClose={() => {}} />))
      const modal = document.querySelector<HTMLElement>('.allwe-quick-video')!
      const updateMetadata = async (width: number, height: number) => {
        const video = modal.querySelector('video')!
        Object.defineProperties(video, {
          videoWidth: { configurable: true, value: width },
          videoHeight: { configurable: true, value: height },
        })
        await act(async () => video.dispatchEvent(new Event('loadedmetadata')))
      }
      expect(modal.style.getPropertyValue('--allwe-video-ratio')).toBe('0.5625')
      expect(modal.style.width).toContain('391px')
      expect(modal.style.width).toContain('80.5dvh')
      expect(modal.style.width).toContain('678.5px')
      expect(modal.style.width).toContain('120px')
      await updateMetadata(720, 1280)
      expect(modal.style.getPropertyValue('--allwe-video-ratio')).toBe('0.5625')
      await updateMetadata(720, 1440)
      expect(modal.style.getPropertyValue('--allwe-video-ratio')).toBe('0.5')
      await updateMetadata(0, 0)
      expect(modal.style.getPropertyValue('--allwe-video-ratio')).toBe('0.5')
      await act(async () => modal.querySelectorAll<HTMLButtonElement>('.allwe-quick-video__tabs button')[1].click())
      expect(modal.style.getPropertyValue('--allwe-video-ratio')).toBe('0.5625')
      await updateMetadata(1920, 1080)
      expect(Number(modal.style.getPropertyValue('--allwe-video-ratio'))).toBeCloseTo(16 / 9)
      expect(modal.querySelector('video')?.controls).toBe(true)
      expect(modal.textContent).toContain('下载原片')
    } finally {
      await act(async () => root.unmount())
      host.remove()
      vi.unstubAllGlobals()
    }
  })
})
