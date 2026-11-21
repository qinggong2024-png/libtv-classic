import { teardownReliabilityDom } from '../../test-helpers/reliabilityDom.mjs'
import { act, StrictMode } from 'react'
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchArtifactObjectUrl, fetchAssetObjectUrl } from '../../api'
import { deferred, mount } from '../../test-helpers/reactHarness'
import { AuthenticatedImage, RemoteVideoPreview, VideoModal, VideoPreview } from './Media'

vi.mock('../../api', () => ({ fetchAssetObjectUrl: vi.fn(), fetchArtifactObjectUrl: vi.fn() }))
let view: Awaited<ReturnType<typeof mount>>
const observers: { callback: IntersectionObserverCallback; disconnect: ReturnType<typeof vi.fn>; observe: ReturnType<typeof vi.fn>; options?: IntersectionObserverInit }[] = []
afterAll(teardownReliabilityDom)
const enter = async (index = observers.length - 1, isIntersecting = true) => {
  await act(async () => { observers[index].callback([{ isIntersecting } as IntersectionObserverEntry], {} as IntersectionObserver) })
}
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  observers.length = 0
  vi.stubGlobal('IntersectionObserver', class {
    disconnect = vi.fn()
    observe = vi.fn()
    constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) { observers.push({ callback, options, disconnect: this.disconnect, observe: this.observe }) }
  })
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.mocked(fetchAssetObjectUrl).mockResolvedValue('blob:asset')
  vi.mocked(fetchArtifactObjectUrl).mockResolvedValue('blob:artifact')
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList)
})
afterEach(async () => {
  await view?.unmount()
  vi.restoreAllMocks()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
})

describe('authenticated image visibility and source ownership', () => {
  it('defers the actual authenticated request until near the viewport, only once', async () => {
    view = await mount(<AuthenticatedImage src="/private/image" alt="Product" />)
    expect(fetchAssetObjectUrl).not.toHaveBeenCalled()
    expect(observers[0].options?.rootMargin).toBe('200px')
    await enter(0, false)
    expect(fetchAssetObjectUrl).not.toHaveBeenCalled()
    await enter()
    expect(fetchAssetObjectUrl).toHaveBeenCalledExactlyOnceWith('/private/image')
    expect(view.host.querySelector('img')?.getAttribute('src')).toBe('blob:asset')
    await enter()
    expect(fetchAssetObjectUrl).toHaveBeenCalledTimes(1)
    expect(observers[0].disconnect).toHaveBeenCalled()
  })
  it.each(['eager', 'no observer'])('loads immediately for %s', async (mode) => {
    if (mode === 'no observer') vi.stubGlobal('IntersectionObserver', undefined)
    view = await mount(<AuthenticatedImage src="/private/image" alt="Product" eager={mode === 'eager'} />)
    expect(fetchAssetObjectUrl).toHaveBeenCalledExactlyOnceWith('/private/image')
    expect(observers).toHaveLength(0)
  })
  it('hides the old image on source change, resets visibility and clears old failures', async () => {
    view = await mount(<AuthenticatedImage src="/a" alt="Product" />)
    await enter()
    await view.render(<AuthenticatedImage src="/b" alt="New product" />)
    expect(view.host.querySelector('img')).toBeNull()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:asset')
    expect(fetchAssetObjectUrl).toHaveBeenCalledTimes(1)
    vi.mocked(fetchAssetObjectUrl).mockRejectedValueOnce(new Error('injected auth fault'))
    await enter()
    expect(view.host.textContent).toContain('图片不可用')
    await view.render(<AuthenticatedImage src="/c" alt="Third product" />)
    expect(view.host.textContent).not.toContain('图片不可用')
    expect(view.host.querySelector('img')).toBeNull()
    await enter()
    expect(view.host.querySelector('img')?.alt).toBe('Third product')
  })
  it('ignores disconnected observers and revokes a blob resolved after unmount', async () => {
    const late = deferred<string>()
    vi.mocked(fetchAssetObjectUrl).mockReturnValue(late.promise)
    view = await mount(<AuthenticatedImage src="/a" alt="Product" />)
    await enter()
    await view.render(null)
    await enter(0)
    await act(async () => { late.resolve('blob:late-image') })
    expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:late-image')
    expect(fetchAssetObjectUrl).toHaveBeenCalledTimes(1)
  })
})

describe('video blob cancellation fault injection', () => {
  it.each(['remote', 'artifact'])('reclaims a late %s response after closing', async (kind) => {
    const late = deferred<string>()
    vi.mocked(kind === 'remote' ? fetchAssetObjectUrl : fetchArtifactObjectUrl).mockReturnValue(late.promise)
    view = await mount(kind === 'remote'
      ? <RemoteVideoPreview mediaUrl="/private/video" title="Video" onClose={() => {}} />
      : <VideoPreview artifactId="artifact" title="Video" onClose={() => {}} />)
    expect(document.querySelector('[role="status"]')?.getAttribute('aria-label')).toBe('视频加载中')
    expect(document.querySelector('[role="status"]')?.textContent).toBe('视频加载中，请稍候')
    await view.render(null)
    await act(async () => { late.resolve('blob:late-video') })
    expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:late-video')
    expect(document.querySelector('video')).toBeNull()
  })
  it('discards out-of-order sources, resets load errors and never shows revoked URLs on A/B/A', async () => {
    const a = deferred<string>()
    const b = deferred<string>()
    vi.mocked(fetchArtifactObjectUrl).mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    view = await mount(<VideoPreview artifactId="a" title="Video" onClose={() => {}} />)
    await view.render(<VideoPreview artifactId="b" title="Video" onClose={() => {}} />)
    await act(async () => { a.resolve('blob:stale-a'); b.reject(new Error('injected download fault')) })
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:stale-a')
    expect(view.host.textContent).toContain('视频加载失败')
    await view.render(<VideoPreview artifactId="a" title="Video" onClose={() => {}} />)
    expect(view.host.textContent).not.toContain('视频加载失败')
    expect(view.host.querySelector('video')?.getAttribute('src')).toBe('blob:artifact')
    const c = deferred<string>()
    vi.mocked(fetchArtifactObjectUrl).mockReturnValue(c.promise)
    await view.render(<VideoPreview artifactId="c" title="Video" onClose={() => {}} />)
    expect(view.host.querySelector('video')).toBeNull()
    await view.render(<VideoPreview artifactId="a" title="Video" onClose={() => {}} />)
    expect(view.host.querySelector('video')).toBeNull()
    await view.render(null)
    await act(async () => { c.resolve('blob:late-shared') })
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:artifact')
  })
  it('reclaims both StrictMode requests and preserves onPlaying forwarding', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    vi.mocked(fetchAssetObjectUrl).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const playing = vi.fn()
    view = await mount(<StrictMode><RemoteVideoPreview mediaUrl="/private/video" title="Video" onClose={() => {}} onPlaying={playing} /></StrictMode>)
    await act(async () => { first.resolve('blob:strict-old'); second.resolve('blob:strict-new') })
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:strict-old')
    await act(async () => { document.querySelector('video')!.dispatchEvent(new Event('playing', { bubbles: true })) })
    expect(playing).toHaveBeenCalledTimes(1)
    await view.render(null)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:strict-new')
  })
})

describe('native player focus and presentation', () => {
  it('lets Tab reach native controls, wraps at guards and restores the trigger on close', async () => {
    const trigger = document.createElement('button')
    document.body.append(trigger)
    trigger.focus()
    const close = vi.fn()
    view = await mount(<VideoModal src="blob:video" title="Video" onClose={close} />)
    const button = view.host.querySelector('button')!
    const video = view.host.querySelector('video')!
    expect(document.activeElement).toBe(button)
    for (const element of [button, video]) {
      element.focus()
      const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
      element.dispatchEvent(tab)
      expect(tab.defaultPrevented).toBe(false)
    }
    const guards = view.host.querySelectorAll<HTMLElement>('[data-dismiss-focus-guard]')
    guards[1].focus()
    expect(document.activeElement).toBe(button)
    guards[0].focus()
    expect(document.activeElement).toBe(video)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    expect(close).not.toHaveBeenCalled()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(close).toHaveBeenCalledTimes(1)
    expect(video.style.position).toBe('static')
    expect(video.style.aspectRatio).toBe('auto')
    expect(view.host.querySelector('.video-stage')?.classList.contains('video-stage--content-fit')).toBe(true)
    await view.render(null)
    expect(document.activeElement).toBe(trigger)
    expect(document.querySelector('[data-dismiss-focus-guard]')).toBeNull()
    trigger.remove()
  })
  it('fits portrait and landscape media to the same dialog without a spare column', async () => {
    const longTitle = 'Woman_smiling_at_Christmas_party_4e119493_2K · 11/13 12:14 · 结果 1'
    view = await mount(<VideoModal src="blob:portrait" title={longTitle} onClose={() => {}} />)
    const dialog = view.host.querySelector<HTMLElement>('[role="dialog"]')!
    expect(dialog.style.width).toBe(view.host.querySelector<HTMLElement>('.video-stage')!.style.width)
    let video = view.host.querySelector('video')!
    Object.defineProperties(video, { videoWidth: { configurable: true, value: 1080 }, videoHeight: { configurable: true, value: 1920 } })
    await act(async () => { video.dispatchEvent(new Event('loadedmetadata')) })
    let stage = view.host.querySelector<HTMLElement>('.video-stage')!
    expect(stage.style.aspectRatio).toContain(String(1080 / 1920))
    expect(stage.style.width).toContain('56.25dvh')
    expect(dialog.style.width).toBe(stage.style.width)
    expect(view.host.querySelector('h2')!.textContent).toBe(longTitle)
    await view.render(<VideoModal src="blob:landscape" title="Edited clip" onClose={() => {}} />)
    expect(dialog.style.width).toBe(view.host.querySelector<HTMLElement>('.video-stage')!.style.width)
    video = view.host.querySelector('video')!
    Object.defineProperties(video, { videoWidth: { configurable: true, value: 1920 }, videoHeight: { configurable: true, value: 1080 } })
    await act(async () => { video.dispatchEvent(new Event('loadedmetadata')) })
    stage = view.host.querySelector<HTMLElement>('.video-stage')!
    expect(parseFloat(stage.style.aspectRatio)).toBeCloseTo(1920 / 1080, 4)
    expect(stage.style.width).toContain('177.778')
    expect(dialog.style.width).toBe(stage.style.width)
  })
  it('clears playback failures when src changes', async () => {
    view = await mount(<VideoModal src="blob:a" title="Video" onClose={() => {}} />)
    await act(async () => { view.host.querySelector('video')!.dispatchEvent(new Event('error')) })
    expect(view.host.textContent).toContain('视频无法播放')
    await view.render(<VideoModal src="blob:b" title="Video" onClose={() => {}} />)
    expect(view.host.querySelector('video')?.getAttribute('src')).toBe('blob:b')
  })
})
