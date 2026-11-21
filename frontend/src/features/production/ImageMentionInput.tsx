import { useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { LocalImage } from '../../shared/components/Media'
import type { Material } from './productionTypes'
import { imageMentionAt, insertImageMention } from './imageMentions'
import { imageMentionAnchor, placeImageMention } from './imageMentionPosition'

export function ImageMentionInput({ value, images, disabled, onChange, label = '复刻要求', placeholder, help, maxLength = 2800 }: {
  value: string; images: Material[]; disabled: boolean; onChange: (value: string) => void
  label?: string; placeholder?: string; help?: string; maxLength?: number
}) {
  const id = useId()
  const input = useRef<HTMLTextAreaElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const [mention, setMention] = useState<ReturnType<typeof imageMentionAt>>(null)
  const [highlighted, setHighlighted] = useState(0)
  const [notice, setNotice] = useState('')
  const open = Boolean(mention && !disabled)
  const selected = Math.min(highlighted, images.length - 1)

  useLayoutEffect(() => {
    const textarea = input.current
    const popup = menu.current
    if (!open || !mention || !textarea || !popup) return
    function position() {
      if (!textarea || !popup || !mention) return
      const anchor = imageMentionAnchor(textarea, mention.start)
      const rect = textarea.getBoundingClientRect()
      const viewport = window.visualViewport
      const bounds = {
        left: viewport?.offsetLeft ?? 0, top: viewport?.offsetTop ?? 0,
        width: viewport?.width ?? document.documentElement.clientWidth,
        height: viewport?.height ?? window.innerHeight,
      }
      popup.style.width = `${Math.min(300, bounds.width - 16)}px`
      popup.style.maxHeight = ''
      const placement = placeImageMention(anchor, bounds, 300, popup.getBoundingClientRect().height)
      Object.assign(popup.style, {
        left: `${placement.left}px`, top: `${placement.top}px`, width: `${placement.width}px`,
        maxHeight: `${placement.maxHeight}px`,
        visibility: anchor.top >= Math.max(rect.top, bounds.top) && anchor.bottom <= Math.min(rect.bottom, bounds.top + bounds.height) && placement.maxHeight > 48 ? 'visible' : 'hidden',
      })
      popup.dataset.side = placement.upward ? 'above' : 'below'
    }
    position()
    const observer = new ResizeObserver(position)
    observer.observe(textarea)
    window.addEventListener('resize', position)
    window.addEventListener('scroll', position, true)
    window.visualViewport?.addEventListener('resize', position)
    window.visualViewport?.addEventListener('scroll', position)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', position)
      window.removeEventListener('scroll', position, true)
      window.visualViewport?.removeEventListener('resize', position)
      window.visualViewport?.removeEventListener('scroll', position)
    }
  }, [open, mention, value, images.length, notice])

  function updateRange(text: string, caret: number) {
    const next = imageMentionAt(text, caret)
    setMention(next)
    if (next?.start !== mention?.start || next?.end !== mention?.end) setHighlighted(0)
    setNotice('')
  }

  function choose(index: number) {
    if (!mention) return
    const next = insertImageMention(value, mention, index)
    if (next.text.length > maxLength) { setNotice('复刻要求已达字数上限，请删减后再插入参考图。'); return }
    onChange(next.text)
    setMention(null)
    requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(next.caret, next.caret) })
  }

  return <div className="video-recreation-field image-mention-field">
    <label className="video-recreation-field" htmlFor={`${id}-input`}><span>{label}</span></label>
    <textarea ref={input} id={`${id}-input`} rows={3} placeholder={placeholder} maxLength={maxLength} value={value} disabled={disabled}
      role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={open ? `${id}-options` : undefined}
      aria-activedescendant={open && selected >= 0 ? `${id}-option-${selected}` : undefined} aria-describedby={`${id}-help`}
      onChange={(event) => { onChange(event.target.value); updateRange(event.target.value, event.target.selectionStart) }}
      onSelect={(event) => updateRange(event.currentTarget.value, event.currentTarget.selectionStart)}
      onBlur={() => setMention(null)}
      onKeyDown={(event) => {
        if (!open || event.nativeEvent.isComposing) return
        if (event.key === 'Escape') { event.preventDefault(); setMention(null) }
        else if (images.length && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
          event.preventDefault()
          setHighlighted((current) => (current + (event.key === 'ArrowDown' ? 1 : -1) + images.length) % images.length)
        } else if (images.length && event.key === 'Enter') { event.preventDefault(); choose(selected) }
      }} />
    {open ? createPortal(<div ref={menu} className="image-mention-menu">
      <strong>选择已添加的参考图</strong>
      {images.length ? <div role="listbox" id={`${id}-options`} aria-label="参考图选择">{images.map(({ id: imageId, file }, index) =>
        <button key={imageId} id={`${id}-option-${index}`} type="button" role="option" aria-selected={selected === index} tabIndex={-1}
          onMouseDown={(event) => event.preventDefault()} onClick={() => choose(index)}>
          <LocalImage file={file} alt={`参考图 ${index + 1}`} /><span><b>@Image {index + 1}</b><small>{file.name}</small></span>
        </button>)}</div> : <p id={`${id}-options`} role="status">请先添加人物或商品参考图。</p>}
      {notice ? <p role="status">{notice}</p> : null}
    </div>, document.body) : null}
    <small id={`${id}-help`}>{help ?? '输入 @ 选择参考图；↑ ↓ 切换，Enter 插入，Esc 关闭。编号与提交顺序一致。'}</small>
  </div>
}
