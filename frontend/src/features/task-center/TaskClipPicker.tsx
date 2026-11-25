import { Check, Play, X } from 'lucide-react'
import { useRef } from 'react'
import { createPortal } from 'react-dom'
import type { Task } from '../../types'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'
import { useDismiss } from '../../shared/hooks/useDismiss'
import type { MultiCutBasketController } from '../production/useMultiCutBasket'
import './taskCenterClipPicker.css'

/**
 * Selecting clips changes only the shared basket. Never submit a paid CUT job
 * from a task card or this picker; submission remains in MultiCutBasket.
 */
export function TaskClipPicker({ task, basket, onClose, onPreview, suspended = false }: {
  task: Task
  basket: MultiCutBasketController
  onClose: () => void
  onPreview: (key: string) => void
  suspended?: boolean
}) {
  const dialogRef = useRef<HTMLElement>(null)
  useDismiss(!suspended, onClose, dialogRef)
  const ready = task.variants.flatMap((variant, index) => variant.downloadArtifactId
    ? [{ variant, index, artifactId: variant.downloadArtifactId }]
    : [])
  const selectedInTask = ready.filter(({ variant }) => basket.isSelected(variant.id)).length
  const count = Math.min(4, Math.max(1, ready.length))
  return createPortal(<div className="allwe-clip-picker__backdrop" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <section ref={dialogRef} className={'allwe-clip-picker allwe-clip-picker--' + count}
      role="dialog" aria-modal={!suspended} aria-hidden={suspended} inert={suspended} aria-label={'选择原片：' + task.title}>
      <header className="allwe-clip-picker__header">
        <div><strong>选择原片</strong><small title={task.title}>{task.title}</small></div>
        <button type="button" className="allwe-clip-picker__close" onClick={onClose} aria-label="关闭选片"><X size={18} /></button>
      </header>
      <div className="allwe-clip-picker__grid">
        {ready.map(({ variant, index, artifactId }) => {
          const selected = basket.isSelected(variant.id)
          const processing = basket.isInProgress(variant.id)
          const disabled = processing || (!selected && basket.atCapacity)
          return <div className={'allwe-clip-picker__clip' + (selected ? ' is-selected' : '')} key={variant.id}>
            <div className="allwe-clip-picker__poster">
              <button type="button" className="allwe-clip-picker__preview"
                onClick={() => onPreview('original:' + variant.id)} aria-label={'播放原片 ' + (index + 1)}>
                <VideoThumbnail artifactId={artifactId} alt={'原片 ' + (index + 1)} />
                <span aria-hidden="true"><Play size={18} fill="currentColor" /></span>
              </button>
              <button type="button" className="allwe-clip-picker__check" aria-pressed={selected}
                disabled={disabled} aria-label={selected ? '移除原片 ' + (index + 1) : '选择原片 ' + (index + 1)}
                onClick={() => basket.toggle(variant.id)}>
                {selected ? <Check size={17} strokeWidth={3} /> : <span />}
              </button>
            </div>
            <div className="allwe-clip-picker__clip-title">
              <strong>原片 {index + 1}</strong>
              {processing ? <small>剪辑中</small> : basket.isPreviouslyCut(variant.id) ? <small>可再次剪辑</small> : null}
            </div>
          </div>
        })}
      </div>
      <footer className="allwe-clip-picker__footer">
        <span>已选 {selectedInTask} / {ready.length} 条</span>
        <button type="button" className="allwe-clip-picker__done" onClick={onClose}>完成</button>
      </footer>
    </section>
  </div>, document.body)
}
