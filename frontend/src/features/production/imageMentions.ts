export const REMOVED_IMAGE_MENTION = '[参考图已移除，请重新选择]'

export function imageMentionAt(text: string, caret: number) {
  const match = /@(?:Image(?:\s*\d*)?)?$/i.exec(text.slice(0, caret))
  return match ? { start: match.index, end: caret } : null
}

export function insertImageMention(text: string, range: { start: number; end: number }, index: number) {
  const token = `@Image ${index + 1} `
  return { text: text.slice(0, range.start) + token + text.slice(range.end), caret: range.start + token.length }
}

export function removeImageMentions(text: string, removedIndex: number) {
  return text.replace(/@Image\s*(\d+)\b/gi, (token, number: string) => {
    const index = Number(number) - 1
    return index === removedIndex ? REMOVED_IMAGE_MENTION : index > removedIndex ? `@Image ${index}` : token
  })
}

export function imageMentionError(text: string, count: number) {
  if (text.includes(REMOVED_IMAGE_MENTION)) return '有参考图已被移除，请在复刻要求中重新选择或删除对应标记。'
  for (const match of text.matchAll(/@Image\s*(\d+)\b/gi)) {
    if (Number(match[1]) < 1 || Number(match[1]) > count) return `${match[0]} 没有对应的参考图，请输入 @ 重新选择。`
  }
  return ''
}
