import type { PromptPresetScope } from './types'

export const SHORT_MOHAIR_IMAGE_PROMPT = '图1模特穿上图2的短马海毛毛衣。领口和下摆都是短马海毛面料，不是罗纹。袖口同为短马海毛面料，不是罗纹，宽松版型，光线统一自然，图2衣服图案版型100%还原'

export const BUILTIN_SHORT_MOHAIR_PRESET_ID = 'builtin-short-mohair'

/**
 * The former video "short mohair" preset copied an example request verbatim.
 * Keep a narrow detector so previously persisted copies can be retired without
 * using the example as generation instructions.
 */
export function isRetiredShortMohairVideoCopy(content: string): boolean {
  const normalized = content.replace(/\s+/g, '')
  return normalized.includes('UGCLifestyleScene')
    && normalized.includes('不要连续一镜到底')
    && normalized.includes('没有螺纹，没有螺纹')
}

export function builtInMaterialPrompt(scope: PromptPresetScope): { id: string; name: string; content: string } | null {
  if (scope === 'image_fusion') {
    return { id: BUILTIN_SHORT_MOHAIR_PRESET_ID, name: '短马海毛', content: SHORT_MOHAIR_IMAGE_PROMPT }
  }
  return null
}
