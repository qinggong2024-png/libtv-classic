import { describe, expect, it } from 'vitest'
import { usagePresentation } from './usagePresentation'

describe('用量统计的用途与单位', () => {
  it.each(['CUTFLOW', null])('剪辑按送剪原片条数展示，category=%s', category => {
    expect(usagePresentation({ kind: 'CUTFLOW', category, units: 2 })).toEqual({ label: '视频剪辑', quantity: '送剪原片 2 条' })
  })
  it('剪辑分类优先于通用媒体类型', () => {
    expect(usagePresentation({ kind: 'VIDEO', category: 'CUTFLOW', units: 2 })).toEqual({ label: '视频剪辑', quantity: '送剪原片 2 条' })
  })
  it('图片按张、视频按秒，保留具体用途', () => {
    expect(usagePresentation({ kind: 'IMAGE', category: 'IMAGE', units: 2 })).toEqual({ label: '图片生成', quantity: '2 张' })
    expect(usagePresentation({ kind: 'VIDEO', category: 'DEPTH_VIDEO', units: 6 })).toEqual({ label: '提取深度视频', quantity: '6 秒' })
    expect(usagePresentation({ kind: 'VIDEO', category: 'VIDEO', units: 1 }).quantity).toBe('1 秒')
  })
  it('历史视频缺少时长时不误报为一秒', () => {
    expect(usagePresentation({ kind: 'VIDEO', units: 1 }).quantity).toBe('时长未记录')
    expect(usagePresentation({ kind: 'VIDEO', units: 6 }).quantity).toBe('6 秒')
  })
  it('未知用量不猜测为图片', () => {
    expect(usagePresentation({ kind: 'UNKNOWN', category: 'NEW_CATEGORY', units: 2 })).toEqual({ label: '其他用量', quantity: '2 单位' })
  })
})
