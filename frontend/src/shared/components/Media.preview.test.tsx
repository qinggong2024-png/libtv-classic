import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { VideoModal } from './Media'

const close = () => {}

describe('shared content-fit video preview', () => {
  it('fits the modal to the intrinsic video instead of imposing a 16:9 canvas', () => {
    const html = renderToStaticMarkup(<VideoModal src="blob:test-portrait" title="原片预览" onClose={close} />)
    expect(html).toContain('video-modal video-modal--content-fit')
    expect(html).toContain('video-stage video-stage--content-fit has-media')
    expect(html).toContain('src="blob:test-portrait"')
    expect(html).toContain('controls=""')
    expect(html).toContain('playsInline=""')
    expect(html).toContain('aria-label="关闭预览"')
  })

  it('keeps a neutral bounded loading surface without an oversized black frame', () => {
    const html = renderToStaticMarkup(<VideoModal title="正在准备" onClose={close} />)
    expect(html).toContain('video-stage video-stage--content-fit')
    expect(html).not.toContain('video-stage--content-fit has-media')
  })

  it('preserves a visible error and close control when media cannot be loaded', () => {
    const html = renderToStaticMarkup(<VideoModal title="失败原片" loadError="视频加载失败" onClose={close} />)
    expect(html).toContain('视频加载失败')
    expect(html).toContain('class="video-error"')
    expect(html).toContain('关闭预览')
  })
})
