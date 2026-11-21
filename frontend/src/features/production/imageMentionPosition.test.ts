import { describe, expect, it } from 'vitest'
import { placeImageMention } from './imageMentionPosition'

describe('image picker viewport collisions', () => {
  const viewport = { left: 0, top: 0, width: 1200, height: 800 }
  it('opens immediately below the active text line', () => {
    expect(placeImageMention({ left: 300, top: 200, bottom: 224 }, viewport, 300, 180))
      .toMatchObject({ left: 300, top: 228, width: 300, upward: false })
  })
  it('flips above the line when the lower viewport has insufficient space', () => {
    const result = placeImageMention({ left: 1050, top: 700, bottom: 724 }, viewport, 300, 180)
    expect(result).toMatchObject({ left: 892, top: 516, upward: true })
    expect(result.top + 180).toBeLessThan(700)
  })
  it('fits the visible phone viewport when a keyboard reduces its size', () => {
    const visible = { left: 10, top: 120, width: 375, height: 300 }
    const result = placeImageMention({ left: 350, top: 300, bottom: 324 }, visible, 300, 320)
    expect(result).toMatchObject({ left: 77, top: 128, maxHeight: 168, upward: true })
    expect(result.left + result.width).toBeLessThanOrEqual(377)
  })
})
