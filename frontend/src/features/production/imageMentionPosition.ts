type Rect = { left: number; top: number; bottom: number }
type Viewport = { left: number; top: number; width: number; height: number }

export function placeImageMention(anchor: Rect, viewport: Viewport, width: number, height: number) {
  const margin = 8
  const gap = 4
  const menuWidth = Math.min(width, Math.max(0, viewport.width - margin * 2))
  const below = Math.max(0, viewport.top + viewport.height - margin - anchor.bottom - gap)
  const above = Math.max(0, anchor.top - gap - viewport.top - margin)
  const upward = below < height && above > below
  const maxHeight = upward ? above : below
  const menuHeight = Math.min(height, maxHeight)
  return {
    left: Math.max(viewport.left + margin, Math.min(anchor.left, viewport.left + viewport.width - margin - menuWidth)),
    top: upward ? anchor.top - gap - menuHeight : anchor.bottom + gap,
    width: menuWidth,
    maxHeight,
    upward,
  }
}

// A mirror shares the textarea's text metrics, including soft wraps and scroll offset.
export function imageMentionAnchor(input: HTMLTextAreaElement, index: number) {
  const style = getComputedStyle(input)
  const mirror = document.createElement('div')
  const properties = [
    'box-sizing', 'border-width', 'border-style', 'padding', 'font-family', 'font-size',
    'font-weight', 'font-style', 'font-variant', 'font-stretch', 'line-height',
    'letter-spacing', 'word-spacing', 'text-transform', 'text-indent', 'text-align',
    'tab-size', 'direction', 'word-break', 'overflow-wrap',
  ]
  for (const property of properties) mirror.style.setProperty(property, style.getPropertyValue(property))
  Object.assign(mirror.style, {
    position: 'fixed', top: '0', left: '0', visibility: 'hidden', pointerEvents: 'none',
    whiteSpace: 'pre-wrap', overflowWrap: 'break-word',
    width: `${input.clientWidth + parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth)}px`,
  })
  const marker = document.createElement('span')
  marker.textContent = input.value[index] || '\u200b'
  mirror.append(document.createTextNode(input.value.slice(0, index)), marker, document.createTextNode(input.value.slice(index + 1)))
  document.body.append(mirror)
  try {
    const inputRect = input.getBoundingClientRect()
    const mirrorRect = mirror.getBoundingClientRect()
    const markerRect = marker.getClientRects()[0] ?? marker.getBoundingClientRect()
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.5
    const top = inputRect.top + markerRect.top - mirrorRect.top - input.scrollTop - (lineHeight - markerRect.height) / 2
    return { left: inputRect.left + markerRect.left - mirrorRect.left - input.scrollLeft, top, bottom: top + lineHeight }
  } finally {
    mirror.remove()
  }
}
