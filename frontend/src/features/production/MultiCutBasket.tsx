// 选片状态独立保留；任务页面默认收起，展开后可拖拽或用方向键排序。
import { ChevronDown, ChevronUp, GripVertical, LoaderCircle, Play, Scissors, X } from 'lucide-react'
import { useState } from 'react'
import type { DragEvent } from 'react'
import { createPortal } from 'react-dom'
import { variantDisplayName } from '../../domain/taskPresentation'
import { GenerationCostBadge } from '../../shared/components/GenerationCostBadge'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'
import type { MultiCutBasketController } from './useMultiCutBasket'
import './cutflow.css'
import './multiCutBasket.css'

const DRAG_TYPE = 'application/x-libtv-cut-source'

export function MultiCutBasket({ basket, onPlay, compact = false }: {
  basket: MultiCutBasketController
  onPlay?: (taskId: string, variantId: string) => void
  compact?: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const [dropActive, setDropActive] = useState(false)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [dropTargetId, setDropTargetId] = useState<string | null>(null)
  const { enabled, selection, entries, blocked, brandConflict, unverifiedBrands, canSubmit, submitHint, submitting, message } = basket
  if (!enabled) return null
  if (!selection.length) return message ? <p className="cut-basket__message" role="status">{message}</p> : null
  if (compact && !expanded) {
    const summary = <section className="cut-basket-compact" aria-label="剪辑篮">
      <Scissors size={17} aria-hidden="true" />
      <span aria-live="polite">已选 {selection.length} 条原片</span>
      <button type="button" className="text-button" aria-expanded={false} aria-label={`展开送剪篮，已选 ${selection.length} 条原片`} onClick={() => setExpanded(true)}>
        {submitting ? '正在送剪' : '展开送剪篮'}<ChevronUp size={16} aria-hidden="true" />
      </button>
    </section>
    return typeof document === 'undefined' ? summary : createPortal(summary, document.body)
  }

  const dropAt = (event: DragEvent, beforeId?: string) => {
    event.preventDefault()
    event.stopPropagation()
    setDropActive(false)
    setDropTargetId(null)
    setDraggingId(null)
    const sourceId = event.dataTransfer.getData(DRAG_TYPE)
    if (sourceId) basket.place(sourceId, beforeId)
  }
  const missing = selection.length - entries.length
  const revision = selection.some(sourceId => basket.isPreviouslyCut(sourceId))

  const basketContent = <section
    className={`cut-basket${dropActive ? ' is-drop-active' : ''}${draggingId ? ' is-sorting' : ''}`}
    aria-label="剪辑篮"
    onDragEnter={(event) => {
      if (!event.dataTransfer.types.includes(DRAG_TYPE)) return
      event.preventDefault()
      setDropActive(true)
    }}
    onDragOver={(event) => {
      if (!event.dataTransfer.types.includes(DRAG_TYPE)) return
      event.preventDefault()
      event.dataTransfer.dropEffect = 'move'
    }}
    onDragLeave={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropActive(false)
    }}
    onDrop={(event) => dropAt(event)}
  >
    <div className="cut-basket__bar">
      <div className="cut-basket__summary" aria-live="polite">
        <span className="cut-basket__count" aria-hidden="true">{selection.length}</span>
        <span className="cut-basket__copy">
          <strong>已选 {selection.length} 条原片{selection.length > 1 ? '，将按顺序合成' : ''}</strong>
          <small>拖拽或使用 ← → 排序</small>
        </span>
      </div>
      <div className="cut-basket__sequence">
        <span className="cut-basket__sequence-label"><GripVertical size={12} aria-hidden="true" />剪辑顺序</span>
        <div
          className="cut-basket__frames"
          role="list"
          aria-label="剪辑输入顺序，从左到右即合成顺序"
          onDragOver={(event) => { event.preventDefault(); setDropTargetId(null) }}
          onDrop={(event) => dropAt(event)}
        >
          {entries.map((entry, index) => <article
            key={entry.variant.id}
            className={`cut-basket__frame${draggingId === entry.variant.id ? ' is-dragging' : ''}${dropTargetId === entry.variant.id ? ' is-drop-target' : ''}`}
            role="listitem"
            tabIndex={0}
            aria-keyshortcuts="ArrowLeft ArrowRight"
            aria-label={`第 ${index + 1} 条：${entry.task.title}。按左右方向键调整顺序`}
            onKeyDown={(event) => {
              if (event.target !== event.currentTarget || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return
              event.preventDefault()
              if (event.key === 'ArrowLeft' && index > 0) basket.place(entry.variant.id, entries[index - 1].variant.id)
              if (event.key === 'ArrowRight' && index < entries.length - 1) basket.place(entry.variant.id, entries[index + 2]?.variant.id)
            }}
            draggable
            title={`第 ${index + 1} 条：${entry.task.title} · ${variantDisplayName(entry.variant)}（可拖拽排序）`}
            onDragStart={(event) => {
              event.dataTransfer.effectAllowed = 'move'
              event.dataTransfer.setData(DRAG_TYPE, entry.variant.id)
              setDraggingId(entry.variant.id)
            }}
            onDragEnd={() => { setDraggingId(null); setDropTargetId(null); setDropActive(false) }}
            onDragOver={(event) => {
              event.preventDefault()
              event.stopPropagation()
              event.dataTransfer.dropEffect = 'move'
              if (entry.variant.id !== draggingId) setDropTargetId(entry.variant.id)
            }}
            onDrop={(event) => dropAt(event, entry.variant.id)}
          >
            <VideoThumbnail artifactId={entry.artifactId} alt={`${entry.task.title} 生成原片`} />
            <span className="cut-basket__ordinal" aria-hidden="true">{index + 1}</span>
            <button
              type="button"
              className="cut-basket__play"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={() => onPlay?.(entry.task.id, entry.variant.id)}
              aria-label={`播放第 ${index + 1} 条：${entry.task.title} · ${variantDisplayName(entry.variant)}`}
              title="播放"
            ><Play size={11} fill="currentColor" /></button>
            <button
              type="button"
              className="cut-basket__remove"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={() => basket.remove(entry.variant.id)}
              aria-label={`移除第 ${index + 1} 条原片`}
              title="移出剪辑篮"
            ><X size={11} /></button>
          </article>)}
        </div>
      </div>
      <div className="cut-basket__actions">
        {compact ? <button type="button" className="text-button cut-basket__minimize" aria-expanded={true} aria-label="收起送剪篮" onClick={() => setExpanded(false)}>收起<ChevronDown size={16} aria-hidden="true" /></button> : null}
        <button type="button" className="secondary-button" onClick={() => basket.clear()}><X size={15} />清空</button>
        <button
          type="button"
          className="primary-button generation-priced-button"
          disabled={!canSubmit}
          onClick={() => void basket.submit()}
          title={submitHint ?? `送 ${selection.length} 条原片去剪辑，预估费用按每条计价、从每日额度扣除`}
        >{submitting ? <LoaderCircle className="spin" size={15} /> : <Scissors size={15} />}<span>{submitting ? '正在送剪' : revision ? `重新剪辑 ${selection.length} 条` : selection.length === 1 ? '送 1 条去剪辑' : `合并 ${selection.length} 条送剪`}</span><GenerationCostBadge category="cutflowPriceUsd" units={selection.length} /></button>
      </div>
    </div>
    {brandConflict ? <p className="cut-basket__alert" role="alert">所选原片分属不同品牌，不能合成同一条片子。请移除其他品牌的素材。</p> : null}
    {unverifiedBrands > 0 ? <p className="cut-basket__alert">有 {unverifiedBrands} 条原片未记录品牌，仍可送剪；服务端会核验品牌，核验不通过时提交会被拒绝。</p> : null}
    {revision ? <p className="cut-basket__alert">本次会创建新的剪辑版本，不会覆盖之前回传的成片。</p> : null}
    {blocked.map(entry => <p className="cut-basket__alert" role="alert" key={entry.variant.id}>{entry.task.title} · {variantDisplayName(entry.variant)} 正在剪辑，请等待结束或移除后再送剪。</p>)}
    {missing > 0 ? <p className="cut-basket__alert" role="alert">有 {missing} 条已选原片不在当前列表中，请移除后再送剪。</p> : null}
    {basket.pendingReads ? <p className="cut-basket__alert" role="status"><LoaderCircle size={14} className="spin" />正在核对所选原片能否送剪…</p> : null}
    {message ? <p className="cut-basket__message" role="status">{message}</p> : null}
  </section>
  return typeof document === 'undefined' ? basketContent : createPortal(basketContent, document.body)
}
