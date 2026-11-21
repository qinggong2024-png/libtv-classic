import { describe, expect, it } from 'vitest'
import { imageMentionAt, imageMentionError, insertImageMention, removeImageMentions, REMOVED_IMAGE_MENTION } from './imageMentions'

describe('uploaded image mentions', () => {
  it('inserts a canonical provider token at the cursor without losing surrounding text', () => {
    const text = '人物用@，保留动作'
    const range = imageMentionAt(text, 4)!
    expect(range).toEqual({ start: 3, end: 4 })
    expect(insertImageMention(text, range, 1)).toEqual({ text: '人物用@Image 2 ，保留动作', caret: 12 })
    expect(imageMentionAt('请用@Image 2', 10)).toEqual({ start: 2, end: 10 })
    expect(imageMentionAt('普通要求', 4)).toBeNull()
  })

  it('keeps remaining references attached to the same image after removal', () => {
    const result = removeImageMentions('人物 @Image 1，商品 @Image 2，场景 @Image 3', 0)
    expect(result).toBe(`人物 ${REMOVED_IMAGE_MENTION}，商品 @Image 1，场景 @Image 2`)
    expect(imageMentionError(result, 2)).toContain('移除')
    expect(imageMentionError('@Image 3', 2)).toContain('没有对应')
    expect(imageMentionError('@Image 0', 2)).toContain('没有对应')
    expect(imageMentionError('@Image 1 与 @Image 2', 2)).toBe('')
  })
})
