import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GenerationAdvancedSettings } from './GenerationAdvancedSettings'

describe('advanced settings scroll restoration', () => {
  const frames: FrameRequestCallback[] = []
  const scrollIntoView = vi.fn()
  const body = { scrollIntoView }
  const details = {
    open: false,
    isConnected: true,
    querySelector: vi.fn(() => body),
  }

  const toggle = (target: object = details) => {
    const element = GenerationAdvancedSettings({
      children: null, summary: '生成设置', customized: false, onReset: vi.fn(),
    })
    element.props.onToggle({ target, currentTarget: details } as never)
  }

  beforeEach(() => {
    frames.length = 0
    scrollIntoView.mockReset()
    details.querySelector.mockClear()
    details.open = false
    details.isConnected = true
    vi.stubGlobal('window', {
      requestAnimationFrame: vi.fn((callback: FrameRequestCallback) => {
        frames.push(callback)
        return frames.length
      }),
      matchMedia: vi.fn(() => ({ matches: false })),
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('scrolls opened content into view after layout on the next animation frame', () => {
    details.open = true
    toggle()
    expect(frames).toHaveLength(1)
    expect(scrollIntoView).not.toHaveBeenCalled()
    frames[0](0)
    expect(details.querySelector).toHaveBeenCalledWith('.generation-advanced__body')
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' })
  })

  it('does not scroll on close, nested events or a rapidly closed panel', () => {
    toggle()
    toggle({})
    expect(frames).toHaveLength(0)
    details.open = true
    toggle()
    details.open = false
    frames[0](0)
    expect(scrollIntoView).not.toHaveBeenCalled()
  })

  it('uses immediate positioning when reduced motion is preferred', () => {
    vi.mocked(window.matchMedia).mockReturnValue({ matches: true } as MediaQueryList)
    details.open = true
    toggle()
    frames[0](0)
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'start' })
  })
})
