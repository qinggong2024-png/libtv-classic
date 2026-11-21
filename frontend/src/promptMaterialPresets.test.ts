import { describe, expect, it } from 'vitest'
import {
  builtInMaterialPrompt,
  isRetiredShortMohairVideoCopy,
  SHORT_MOHAIR_IMAGE_PROMPT,
} from './promptMaterialPresets'

describe('prompt material presets', () => {
  it('keeps the image preset and leaves video generation to the automatic compiler', () => {
    expect(builtInMaterialPrompt('image_fusion')?.content).toBe(SHORT_MOHAIR_IMAGE_PROMPT)
    expect(builtInMaterialPrompt('video')).toBeNull()
    expect(builtInMaterialPrompt('image_storyboard')).toBeNull()
  })

  it('keeps the image prompt requirements and retires the copied video example', () => {
    expect(SHORT_MOHAIR_IMAGE_PROMPT).toContain('领口和下摆都是短马海毛面料，不是罗纹')
    expect(SHORT_MOHAIR_IMAGE_PROMPT).toContain('袖口同为短马海毛面料，不是罗纹')
    expect(SHORT_MOHAIR_IMAGE_PROMPT).not.toContain('螺纹')
    expect(SHORT_MOHAIR_IMAGE_PROMPT).toContain('图2衣服图案版型100%还原')
    expect(isRetiredShortMohairVideoCopy(
      '保持参考图氛围，UGC Lifestyle Scene，环境保持不变，不要连续一镜到底，没有螺纹，没有螺纹。',
    )).toBe(true)
    expect(isRetiredShortMohairVideoCopy('人物按输入图自然展示当前服装。')).toBe(false)
    expect(isRetiredShortMohairVideoCopy(
      '生成一段动态视频，智能分镜。iPhone17后置拍摄效果，100%还原球衣。',
    )).toBe(false)
  })
})
