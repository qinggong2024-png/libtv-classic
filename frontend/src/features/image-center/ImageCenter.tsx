import { errorMessage, plainText, userFacingError } from '../../errorPresentation'
import {
  AlertTriangle, ArrowRight, Check, CheckCircle2, ChevronDown, ChevronUp, Clock3, Columns2, Film, GripVertical,
  Download, Image as ImageIcon, Images, Layers3, LoaderCircle, Package, Plus, RotateCcw, Save,
  Sparkles, WandSparkles, X,
} from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent, ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { IMAGE_FILE_ACCEPT, isSupportedImageFile } from '../../imageFiles'
import { ApiError, createImageGenerationBatch, fetchAssetObjectUrl, getImageGenerationBatch, querySubmissionRecovery, saveAsset } from '../../api'
import { GenerationBrief } from '../../shared/components/GenerationAdvancedSettings'
import {
  MAX_IMAGE_CENTER_OUTFITS, imageModelLabel, restoreImageDefaultPrompt,
  buildImageGenerationBatchRequest, buildWaitingTasks, calculateImageTaskSummary, compactImageAssetSet,
  createImageCenterDraft, createImageCenterRegenerationDraft, createOutfit, getImageAssetSlots, getOrderedSelectedTasks, imageAssetSetFromSlots,
  moveImageAssetSlot, normalizeImageGenerationModes, normalizeImageGenerationQuality, parseStoryboardPrompt, validateImageCenterDraft,
} from '../../imageCenterModel'
import type { ImageGenerationBatch } from '../../types'
import { mergeImageHistory } from '../../imageCenterHistory'
import { useImageHistory } from '../../useImageHistory'
import type {
  ImageAssetSet, ImageCenterDraft, ImageCenterDraftSnapshot, ImageGenerationMode, ImageGenerationOutput,
  ImageGenerationTask, OutfitDraft,
} from '../../imageCenterModel'
import { loadImageCenterDraft, saveImageCenterDraft } from '../../imageCenterStore'
import { createUuid } from '../../uuid'
import { imageTaskNotificationForTransition } from '../../browserNotifications'
import type { TaskNotification } from '../../browserNotifications'
import { FoundationDrawer } from '../../shared/components/ui'
import { imageStatusPresentation } from '../../domain/statusPresentation'
import { parseImageOutputDrag, prepareImageOutputReference } from './imageOutputReuse'
import './imageReview.css'
import './imageComposer.css'
import './creationV9.css'
import '../../shared/components/creationPolish.css'
import { GenerationCostBadge } from '../../shared/components/GenerationCostBadge'
import { AccountSiteField } from '../production/CutflowSiteSelect'
import { useAccountSiteSelection } from '../../useAccountSiteSelection'
import { accountSiteSelection } from '../../accountSiteSelection'
import { getImageCenterVideoStoryboardLimit } from '../../imageCenterVideoSeed'
import { resolveImageCenterReferences } from '../../imageCenterReferences'
import { changeImageComparison, imageComparisonIds } from './imageComparison'

export interface ImageCenterVideoHandoff {
  task: ImageGenerationTask
  outputs: ImageGenerationOutput[]
}

export interface ImageCenterProps {
  canMakeVideos?: boolean
  active: boolean
  draftScope: string | null
  openTaskId: string | null
  onPrepareVideos: (handoffs: ImageCenterVideoHandoff[], onSubmitted: () => void) => void | Promise<void>
  onNotifications: (events: ImageCenterNotificationEvent[]) => void
  onActiveTasksChange: (active: boolean) => void
}

export interface ImageCenterNotificationEvent {
  taskId: string
  status: ImageGenerationTask['status']
  notification: TaskNotification
}

const ASSET_SLOT_DRAG_TYPE = 'application/x-image-center-slot'
const IMAGE_OUTPUT_DRAG_TYPE = 'application/x-image-center-output'
const TERMINAL_IMAGE_STATUSES = new Set(['completed', 'failed', 'write_uncertain', 'manual_review'])
type GenerationAvailability = 'checking' | 'available' | 'unavailable'

function isGenerationUnavailableError(cause: unknown): boolean {
  return cause instanceof ApiError
    && (cause.code === 'MAINTENANCE_QUERY_ONLY' || cause.status === 503 || cause.status === 403)
}

function generationUnavailableMessage(cause: unknown): string {
  if (cause instanceof ApiError && cause.code === 'MAINTENANCE_QUERY_ONLY') return '当前处于维护模式，仅支持登录和查询已保存任务，生成暂不可用。'
  if (cause instanceof ApiError && cause.status === 403) return '当前账号暂无生图权限，生成任务暂不可用，请联系管理员。'
  return '当前无法使用生图服务，创建任务已暂停，请稍后再试或联系管理员。'
}

function mergeBatchIntoTasks(tasks: ImageGenerationTask[], batch: ImageGenerationBatch): ImageGenerationTask[] {
  return mergeImageHistory(tasks, [batch])
}

function useFileUrl(file: File | null): string {
  const [url, setUrl] = useState('')
  useEffect(() => {
    let active = true
    if (!file) {
      queueMicrotask(() => { if (active) setUrl('') })
      return () => { active = false }
    }
    const nextUrl = URL.createObjectURL(file)
    queueMicrotask(() => { if (active) setUrl(nextUrl) })
    return () => {
      active = false
      URL.revokeObjectURL(nextUrl)
    }
  }, [file])
  return url
}

function AssetPreview({ file, url = '', alt }: { file: File | null; url?: string; alt: string }) {
  const source = useFileUrl(file) || url
  return source ? <img src={source} alt={alt} draggable={false} /> : <ImageIcon aria-hidden="true" size={22} />
}

function AuthenticatedAssetPreview({ file, url, alt }: { file: File | null; url: string; alt: string }) {
  const [authenticatedUrl, setAuthenticatedUrl] = useState('')
  useEffect(() => {
    let active = true
    let objectUrl = ''
    if (!url || file) {
      queueMicrotask(() => { if (active) setAuthenticatedUrl('') })
      return () => { active = false }
    }
    void fetchAssetObjectUrl(url).then((nextUrl) => {
      objectUrl = nextUrl
      if (active) setAuthenticatedUrl(nextUrl)
      else URL.revokeObjectURL(nextUrl)
    }).catch(() => {
      if (active) setAuthenticatedUrl('')
    })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [file, url])
  return <AssetPreview file={file} url={authenticatedUrl} alt={alt} />
}

function DraftTabName({ item, selected, disabled, onSelect, onRename }: {
  item: ImageCenterDraftSnapshot
  selected: boolean
  disabled: boolean
  onSelect: () => void
  onRename: (name: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState(false)
  const [value, setValue] = useState(item.name)
  const [labelWidth, setLabelWidth] = useState<number>()
  const inputRef = useRef<HTMLInputElement>(null)
  const tabRef = useRef<HTMLButtonElement>(null)
  const ended = useRef(false)
  const count = item.outfits.filter(outfit => outfit.front).length
  // Both layers remain mounted, so the label and editor can cross-fade while
  // the same tab animates to its new width. The label is measured independently.
  useLayoutEffect(() => {
    const tab = tabRef.current
    if (!tab) return
    const measure = () => setLabelWidth(Math.ceil(tab.getBoundingClientRect().width))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(tab)
    return () => observer.disconnect()
  }, [item.name, count])
  useLayoutEffect(() => {
    if (editing) { inputRef.current?.focus({ preventScroll: true }); inputRef.current?.select() }
  }, [editing])
  const start = () => {
    if (disabled || editing) return
    ended.current = false
    setSaved(false)
    setValue(item.name)
    setEditing(true)
  }
  const finish = (save: boolean, restoreFocus = false) => {
    if (ended.current || !editing) return
    ended.current = true
    const name = value.trim()
    if (save && !disabled && name && name !== item.name) {
      onRename(name)
      setSaved(true)
    }
    setEditing(false)
    if (restoreFocus) window.requestAnimationFrame(() => tabRef.current?.focus({ preventScroll: true }))
  }
  return <div className="v9-draft-name-motion" data-editing={editing} data-saved={saved}
    style={{ width: editing ? Math.max(174, labelWidth ?? 0) : labelWidth }}
    onAnimationEnd={() => setSaved(false)}>
    <button ref={tabRef} type="button" role="tab" aria-selected={selected} aria-hidden={editing || undefined}
      tabIndex={editing ? -1 : undefined} disabled={disabled}
      onClick={onSelect} onDoubleClick={start} onKeyDown={event => { if (event.key === 'F2') { event.preventDefault(); start() } }}
      title={item.name + ' · 双击重命名'}><span className="v9-draft-name">{item.name}</span><small>{count}</small></button>
    <div className="v9-draft-name-edit" aria-hidden={!editing}>
      <input ref={inputRef} aria-label="批次名称" value={value} maxLength={100} disabled={disabled || !editing} tabIndex={editing ? 0 : -1}
        onChange={event => setValue(event.target.value)}
        onBlur={() => finish(true)}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing || event.keyCode === 229) return
          if (event.key === 'Enter' || event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(event.key === 'Enter', true) }
        }} />
    </div>
  </div>
}

function OrderedAssetUpload({ idPrefix, title, headerAccessory, assets, onChange, onPreview }: {
  idPrefix: string
  title: string
  headerAccessory?: ReactNode
  assets: ImageAssetSet
  onChange: (assets: ImageAssetSet) => void
  onPreview?: (file: File, label: string) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const files = getImageAssetSlots(assets).filter((file): file is File => file !== null)
  const count = files.length
  const receive = (files: File[]) => {
    const accepted = files.filter(isSupportedImageFile)
    if (!accepted.length) return
    onChange(imageAssetSetFromSlots([...getImageAssetSlots(assets).filter((file): file is File => file !== null), ...accepted].slice(0, 3)))
  }
  const change = (event: ChangeEvent<HTMLInputElement>) => {
    receive(Array.from(event.currentTarget.files ?? []))
    event.currentTarget.value = ''
  }
  const remove = (index: number) => {
    onChange(imageAssetSetFromSlots(files.filter((_, fileIndex) => fileIndex !== index)))
  }
  const move = (from: number, to: number) => onChange(moveImageAssetSlot(assets, from, to))
  const handleDrop = (event: DragEvent, target: number) => {
    event.preventDefault()
    event.stopPropagation()
    const slotPayload = event.dataTransfer.getData(ASSET_SLOT_DRAG_TYPE)
    if (slotPayload) {
      try {
        const source = JSON.parse(slotPayload) as { owner?: string; index?: number }
        if (source.owner === idPrefix && source.index !== undefined && Number.isInteger(source.index)) move(source.index, target)
      } catch {
        // Ignore malformed drag data from outside this uploader.
      }
      return
    }
    receive(Array.from(event.dataTransfer.files))
  }

  return <section
    className={`ordered-asset-upload asset-count-${count}${count ? ' has-files' : ''}${headerAccessory ? ' has-header-accessory' : ''}`}
    aria-label={title}
    onDragOver={(event) => {
      event.preventDefault()
      event.dataTransfer.dropEffect = event.dataTransfer.types.includes(ASSET_SLOT_DRAG_TYPE) ? 'move' : 'copy'
    }}
    onDrop={(event) => {
      handleDrop(event, files.length)
    }}
  >
    <header>
      <div className="ordered-asset-upload__title"><strong>{title}</strong></div>
      {headerAccessory}
      <span>{count}/3</span>
    </header>
    <div className={`ordered-asset-list${count ? '' : ' is-empty'}`} aria-label={`${title}排序列表`}>
      {files.map((file, index) => <article
        key={`${idPrefix}-${index}-${file.name}-${file.size}-${file.lastModified}`}
        className="ordered-asset-card"
        title={`参考图 ${index + 1}：${file.name}`}
        draggable
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = 'move'
          event.dataTransfer.setData(ASSET_SLOT_DRAG_TYPE, JSON.stringify({ owner: idPrefix, index }))
          event.currentTarget.classList.add('is-dragging')
        }}
        onDragEnd={(event) => event.currentTarget.classList.remove('is-dragging')}
        onDragOver={(event) => {
          event.preventDefault()
          event.dataTransfer.dropEffect = event.dataTransfer.types.includes(ASSET_SLOT_DRAG_TYPE) ? 'move' : 'copy'
        }}
        onDrop={(event) => handleDrop(event, index)}
      >
        {onPreview ? <button type="button" className="ordered-asset-card__media creation-reference-preview" aria-label={"预览" + title + " " + (index + 1)} onClick={() => onPreview(file, title + " " + (index + 1))}><AssetPreview file={file} alt={file.name} /></button> : <span className="ordered-asset-card__media"><AssetPreview file={file} alt={`参考图 ${index + 1}：${file.name}`} /></span>}
        <span className="ordered-asset-card__role"><i>{index + 1}</i>参考图</span>
        <button type="button" className="ordered-asset-card__remove" onClick={(event) => { event.stopPropagation(); remove(index) }} onPointerDown={(event) => event.stopPropagation()} aria-label={`移除参考图 ${index + 1}`} title="删除图片"><X size={14} /></button>
      </article>)}
      {count < 3 ? <button type="button" className="ordered-asset-dropzone" aria-label={`上传${title}`} onClick={() => inputRef.current?.click()}>
        <span>{<Plus size={20} />}</span>
        <span><strong>{title === '共用模特参考' ? '添加' : count ? '添加图片' : '点击或拖入'}</strong><small>JPG / PNG / WEBP · 最多 3 张</small></span>
      </button> : null}
    </div>
    <input ref={inputRef} id={`${idPrefix}-files`} className="sr-only" type="file" multiple accept={IMAGE_FILE_ACCEPT} disabled={count >= 3} onChange={change} />
  </section>
}

function productLabel(outfit: OutfitDraft, index: number) {
  const number = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'][index] ?? String(index + 1)
  return !outfit.name.trim() || /^套装 \d+$/.test(outfit.name) ? `产品${number}` : outfit.name
}

function OutfitCard({ outfit, label, onChange }: {
  outfit: OutfitDraft
  label: string
  onChange: (outfit: OutfitDraft) => void
}) {
  return <article className="outfit-card">
    <OrderedAssetUpload
      idPrefix={outfit.id}
      title="服装图片"
      headerAccessory={<span className="outfit-current-name" title={label}>{label}</span>}
      assets={outfit}
      onChange={(assets) => onChange({ ...outfit, ...assets })}
    />
  </article>
}

function SourceThumb({ file, label, onPreview }: {
  file: File | null
  label: string
  onPreview: (file: File, label: string) => void
}) {
  if (!file) return null

  return <figure className="image-task-source">
    <button
      type="button"
      onClick={() => onPreview(file, label)}
      aria-label={`查看${label}大图`}
      title="查看大图"
    ><AssetPreview file={file} alt={`${label}：${file.name}`} /></button>
    <figcaption>{label}</figcaption>
  </figure>
}

function OutputSelectionButton({ selected, label, className, onToggle }: {
  selected: boolean
  label: string
  className: string
  onToggle: () => void
}) {
  return <button
    type="button"
    className={`${className}${selected ? ' is-selected' : ''}`}
    aria-label={selected ? `取消选择${label}视频素材` : `选择${label}用于制作视频`}
    aria-pressed={selected}
    title={selected ? '取消选择' : '选择制作视频'}
    onClick={(event) => { event.stopPropagation(); onToggle() }}
  >{selected ? <Check size={17} strokeWidth={2.6} /> : null}</button>
}

export function OutputCard({ taskId, output, selected, onToggle, onPreview, onReuse, reusing = false }: {
  taskId: string
  output: ImageGenerationOutput
  selected: boolean
  onToggle: () => void
  onPreview: () => void
  onReuse?: () => void
  reusing?: boolean
}) {
  const download = () => {
    if (output.url) void saveAsset(output.downloadUrl || output.url, `${output.label}.png`)
  }
  return <article
    className={`image-output-card${selected ? ' is-selected' : ''}${onReuse ? ' has-reuse' : ''}`}
    draggable
    onDragStart={(event) => {
      event.dataTransfer.effectAllowed = 'copyMove'
      event.dataTransfer.setData(IMAGE_OUTPUT_DRAG_TYPE, JSON.stringify({ taskId, outputId: output.id }))
      event.currentTarget.classList.add('is-dragging')
    }}
    onDragEnd={(event) => event.currentTarget.classList.remove('is-dragging')}
    title={onReuse ? '拖到“批量创作”继续编辑，或拖入选片工作台' : '拖入选片工作台'}
  >
    <button type="button" className="image-output-card__preview" onClick={onPreview} aria-label={`查看${output.label}大图`} title="查看大图">
      <span className="image-output-card__media"><AuthenticatedAssetPreview file={output.file} url={output.url} alt={output.label} /></span>
      <span><strong>{output.label}</strong><small>{output.kind === 'fusion' ? '多图融合' : '分镜图'}</small></span>
    </button>
    <OutputSelectionButton selected={selected} label={output.label} className="image-output-card__selection" onToggle={onToggle} />
    {output.url ? <button type="button" className="image-output-card__download" onClick={download} title="下载图片" aria-label={`下载${output.label}`}><Download size={14} /></button> : null}
    {onReuse ? <button type="button" className="image-output-card__reuse" disabled={reusing || (!output.file && !output.downloadUrl && !output.url)} onClick={onReuse} aria-label={`继续编辑${output.label}`} title="将结果图用作模特参考，可继续编辑或生成分镜图">{reusing ? <LoaderCircle className="spin" size={14} /> : <WandSparkles size={14} />}{reusing ? '正在载入' : '继续编辑'}</button> : null}
  </article>
}

function AssetPreviewDialog({ file, url = '', label, selection, onClose }: {
  file: File | null
  url?: string
  label: string
  selection?: { selected: boolean; onToggle: () => void }
  onClose: () => void
}) {
  const dialogRef = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current()
    }
    document.body.classList.add('modal-open')
    document.addEventListener('keydown', closeOnEscape)
    dialogRef.current?.focus()
    return () => {
      document.body.classList.remove('modal-open')
      document.removeEventListener('keydown', closeOnEscape)
      previouslyFocused?.focus()
    }
  }, [])

  return createPortal(<div
    className="image-output-preview-backdrop"
    onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}
  >
    <section ref={dialogRef} className="image-output-preview" role="dialog" aria-modal="true" aria-labelledby="image-output-preview-title" tabIndex={-1}>
      <h2 id="image-output-preview-title" className="sr-only">{label}大图预览</h2>
      <button type="button" className="image-output-preview__stage" onClick={onClose} aria-label={`关闭${label}大图预览`} title="关闭大图">
        <AuthenticatedAssetPreview file={file} url={url} alt={label} />
      </button>
      <button type="button" className="image-output-preview__close" onClick={onClose} aria-label="关闭大图预览" title="关闭大图"><X size={20} /></button>
      {selection ? <OutputSelectionButton selected={selection.selected} label={label} className="image-output-preview__selection" onToggle={selection.onToggle} /> : null}
    </section>
  </div>, document.body)
}

function OutputPreview({ output, selected, onToggle, onClose }: {
  output: ImageGenerationOutput
  selected: boolean
  onToggle: () => void
  onClose: () => void
}) {
  return <AssetPreviewDialog
    file={output.file}
    url={output.url}
    label={output.label}
    selection={{ selected, onToggle }}
    onClose={onClose}
  />
}

export function ImageOutputCompare({ outputs, initialIds = [], sources = {}, onClose }: {
  outputs: ImageGenerationOutput[]
  initialIds?: string[]
  sources?: Record<string, string>
  onClose: () => void
}) {
  const [comparisonIds, setComparisonIds] = useState(() => imageComparisonIds(outputs, initialIds))
  const resolvedIds = imageComparisonIds(outputs, comparisonIds)
  if (outputs.length < 2) return null
  return <FoundationDrawer className="creation-drawer"
    open
    title="并排比较"
    onClose={onClose}
  >
    <p>切换左右图片仅用于比较，视频选片及顺序保持不变。</p>
    <div className="image-compare-grid" aria-label="图片并排比较">
      {resolvedIds.map((id, side) => {
        const output = outputs.find((item) => item.id === id)!
        return <article className="image-compare-panel" key={side}>
        <label className="image-compare-selector">{side === 0 ? '左侧图片' : '右侧图片'}
          <select value={id} onChange={(event) => setComparisonIds(changeImageComparison(resolvedIds, side, event.target.value))}>
            {outputs.map((item) => <option key={item.id} value={item.id}>{item.label} · {sources[item.id] || item.id}</option>)}
          </select>
        </label>
        <div className="image-compare-panel__media">
          <AuthenticatedAssetPreview file={output.file} url={output.url} alt={output.label} />
        </div>
        <div className="image-compare-panel__meta">
          <strong>{output.label}</strong>
          <span>{output.kind === 'fusion' ? '换装结果' : '分镜图'} · {output.view === 'shot' ? '镜头' : '结果'}</span>
          <span>来源：{sources[output.id] || output.id}</span>
        </div>
      </article>})}
    </div>
  </FoundationDrawer>
}

function ImageTaskDetails({ task, children }: { task: ImageGenerationTask; children?: ReactNode }) {
  return <details className="image-task-technical">
    <summary>任务详情</summary>
    <dl>
      <dt>产品</dt><dd>{task.outfitName}{task.sku ? ` · ${task.sku}` : ''}</dd>
      <dt>创建时间</dt><dd>{new Date(task.createdAt).toLocaleString('zh-CN')}</dd>
      <dt>任务编号</dt><dd>{task.id}</dd>
      <dt>本批任务编号</dt><dd>{task.batchId ?? task.clientBatchId ?? '尚未分配'}</dd>
      <dt>生成服务查询编号</dt><dd>{task.providerTaskIds.length ? task.providerTaskIds.join(', ') : '尚未分配'}</dd>
      <dt>生成模型</dt><dd>{imageModelLabel(task.provider, task.modelId)}</dd>
      {task.errorCode ? <><dt>问题说明</dt><dd>{userFacingError({ code: task.errorCode, message: task.error })}</dd></> : null}
    </dl>
    {children}
  </details>
}

function SelectionWorkbench({ tasks, selectedOutputIds, expanded, preparing, onExpandedChange, onClear, onRemove, onPlaceOutput, onGenerate }: {
  tasks: ImageGenerationTask[]
  selectedOutputIds: string[]
  expanded: boolean
  preparing: boolean
  onExpandedChange: (expanded: boolean) => void
  onClear: () => void
  onRemove: (outputId: string) => void
  onPlaceOutput: (taskId: string, outputId: string, beforeOutputId?: string) => void
  onGenerate: () => void
}) {
  const selectedTasks = useMemo(() => getOrderedSelectedTasks(tasks, selectedOutputIds), [selectedOutputIds, tasks])
  const selectedStoryboardCapacity = selectedTasks.reduce(
    (total, { task }) => total + getImageCenterVideoStoryboardLimit(task),
    0,
  )
  const [dropActive, setDropActive] = useState(false)
  const parseOutputDrop = (event: DragEvent) => {
    const payload = event.dataTransfer.getData(IMAGE_OUTPUT_DRAG_TYPE)
    if (!payload) return null
    try {
      const parsed = JSON.parse(payload) as { taskId?: string; outputId?: string }
      return parsed.taskId && parsed.outputId ? { taskId: parsed.taskId, outputId: parsed.outputId } : null
    } catch { return null }
  }
  const dropOutput = (event: DragEvent, beforeOutputId?: string) => {
    event.preventDefault()
    event.stopPropagation()
    setDropActive(false)
    const payload = parseOutputDrop(event)
    if (payload) onPlaceOutput(payload.taskId, payload.outputId, beforeOutputId)
  }

  return <section
    className={`image-selection-bar${expanded ? ' is-expanded' : ''}${dropActive ? ' is-drop-active' : ''}`}
    aria-label="选片工作台"
    onDragEnter={(event) => {
      if (!event.dataTransfer.types.includes(IMAGE_OUTPUT_DRAG_TYPE)) return
      event.preventDefault()
      setDropActive(true)
    }}
    onDragOver={(event) => {
      if (!event.dataTransfer.types.includes(IMAGE_OUTPUT_DRAG_TYPE)) return
      event.preventDefault()
      event.dataTransfer.dropEffect = 'move'
    }}
    onDragLeave={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropActive(false)
    }}
    onDrop={(event) => dropOutput(event)}
  >
    <div className="image-selection-bar__header" role="toolbar" aria-label="已选图片操作">
      <div className="image-selection-bar__summary" aria-live="polite">
        <span className="image-selection-bar__count" aria-hidden="true">{selectedOutputIds.length}</span>
        <span className="image-selection-bar__copy"><strong>已选择 {selectedOutputIds.length} 张图片</strong><small>{selectedTasks.length} 套产品可生成视频</small></span>
      </div>
      <button type="button" className="icon-button image-selection-bar__toggle" onClick={() => onExpandedChange(!expanded)} aria-expanded={expanded} aria-controls="image-selection-details" aria-label={expanded ? '收起选片详情' : '展开选片详情'} title={expanded ? '收起详情' : '展开详情'}>{expanded ? <ChevronDown size={18} /> : <ChevronUp size={18} />}</button>
      <div className="image-selection-bar__actions">
        <button type="button" className="secondary-button" onClick={onClear}><X size={16} />取消选择</button>
        <button type="button" className="primary-button" disabled={preparing} onClick={onGenerate}>{preparing ? <LoaderCircle className="spin" size={17} /> : <Film size={17} />}{preparing ? '正在准备' : '下一步：制作视频'}</button>
      </div>
    </div>
    <div id="image-selection-details" className="image-selection-bar__details" aria-hidden={!expanded} inert={!expanded}>
      <div className="image-selection-bar__details-inner">
        <header><div><strong>分镜编排</strong><small>共 {selectedTasks.length} 个产品视频</small></div><span>{selectedOutputIds.length} / {selectedStoryboardCapacity} 张</span></header>
        <div className="image-selection-groups">{selectedTasks.map(({ task, outputs }) => {
          const storyboardLimit = getImageCenterVideoStoryboardLimit(task)
          return <article key={task.id} className="image-selection-group">
            <div className="image-selection-frames" role="list" aria-label={`${task.outfitName}分镜顺序`} onDragOver={(event) => event.preventDefault()} onDrop={(event) => dropOutput(event)}>
              {outputs.map((output, index) => <article
                key={output.id}
                className="image-selection-frame"
                role="listitem"
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.effectAllowed = 'move'
                  event.dataTransfer.setData(IMAGE_OUTPUT_DRAG_TYPE, JSON.stringify({ taskId: task.id, outputId: output.id }))
                }}
                onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = 'move' }}
                onDrop={(event) => dropOutput(event, output.id)}
              >
                <span><AuthenticatedAssetPreview file={output.file} url={output.url} alt={`${task.outfitName}分镜 ${index + 1}`} /></span>
                <i>{index + 1}</i>
                <button type="button" onClick={() => onRemove(output.id)} aria-label={`移除分镜 ${index + 1}`} title="移除分镜"><X size={12} /></button>
                <GripVertical size={13} aria-hidden="true" />
              </article>)}
              {outputs.length < storyboardLimit ? <div className="image-selection-drop-slot" aria-hidden="true"><Plus size={17} /><span>{outputs.length ? '添加分镜' : '暂无分镜'}</span></div> : null}
            </div>
            <div className="image-selection-product">
              <span><AssetPreview file={task.product.front} alt={`${task.outfitName}服装参考图`} /><i>服装</i></span>
              <div><strong>{task.outfitName}</strong><small>{task.sku || '未填写 SKU'}</small></div>
              <em>{outputs.length}/{storyboardLimit}</em>
            </div>
          </article>
        })}</div>
      </div>
    </div>
  </section>
}

export function ImageCenter(props: ImageCenterProps) {
  return <ScopedImageCenter key={props.draftScope ?? 'anonymous'} {...props} />
}

function snapshotDraft(draft: ImageCenterDraft): ImageCenterDraftSnapshot {
  const { id, name, siteProfileId, model, outfits, config } = draft
  return { id, name, siteProfileId, model, outfits, config }
}
function blankDraft(): ImageCenterDraftSnapshot {
  return snapshotDraft(createImageCenterDraft(createUuid(), createUuid()))
}
function activateDraft(current: ImageCenterDraft, next: ImageCenterDraftSnapshot, pendingDrafts: ImageCenterDraftSnapshot[]): ImageCenterDraft {
  return { ...current, ...next, siteProfileId: next.siteProfileId, pendingDrafts, activeTab: 'create' }
}

function ScopedImageCenter({ canMakeVideos = true, active, draftScope, openTaskId, onPrepareVideos, onNotifications, onActiveTasksChange }: ImageCenterProps) {
  const { choices, error: siteError } = useAccountSiteSelection()
  const [draft, setDraft] = useState<ImageCenterDraft>(() => createImageCenterDraft(createUuid(), createUuid()))
  const siteProfileId = choices ? accountSiteSelection(choices, draft.siteProfileId) : ''
  const [activeOutfitId, setActiveOutfitId] = useState('')
  const [editingOutfit, setEditingOutfit] = useState<string | null>(null)
  const productInput = useRef<HTMLInputElement>(null)
  const namedProductInput = useRef<HTMLInputElement>(null)
  const submissionLock = useRef(false)
  const [recordBatch, setRecordBatch] = useState<string | null>(null)
  const [recordSearch, setRecordSearch] = useState('')
  const [recordFilter, setRecordFilter] = useState('all')
  const [loaded, setLoaded] = useState(false)
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'error'>('saved')
  const [notice, setNotice] = useState('')
  const [preparingVideos, setPreparingVideos] = useState(false)
  const [selectionBarExpanded, setSelectionBarExpanded] = useState(false)
  const [previewOutputId, setPreviewOutputId] = useState<string | null>(null)
  const [previewSource, setPreviewSource] = useState<{ file: File; label: string } | null>(null)
  const [compareOpen, setCompareOpen] = useState(false)
  const [recordView, setRecordView] = useState<'gallery' | 'tasks'>('gallery')
  const [desktop, setDesktop] = useState(() => window.matchMedia('(min-width: 1024px)').matches)
  const [createDropActive, setCreateDropActive] = useState(false)
  const [reusingOutputId, setReusingOutputId] = useState<string | null>(null)
  const reuseRequestRef = useRef(0)
  const [submitting, setSubmitting] = useState(false)
  const [generationAvailability, setGenerationAvailability] = useState<GenerationAvailability>('checking')
  const [generationAvailabilityReason, setGenerationAvailabilityReason] = useState('')
  const [connectionRevision, setConnectionRevision] = useState(0)
  const [pollingErrors, setPollingErrors] = useState<Record<string, string>>({})
  const saveTimerRef = useRef<number | null>(null)
  const latestDraftRef = useRef(draft)
  const loadedRef = useRef(false)
  const previousTaskStatusesRef = useRef<Map<string, ImageGenerationTask['status']>>(new Map())
  const notificationBaselineReadyRef = useRef(false)
  const receiveHistory = useCallback((batches: ImageGenerationBatch[]) => {
    setDraft((current) => ({ ...current, tasks: mergeImageHistory(current.tasks, batches) }))
  }, [])
  const history = useImageHistory(draftScope, loaded, receiveHistory)
  const recheckConnection = useCallback(() => {
    setGenerationAvailability('checking')
    setConnectionRevision((revision) => revision + 1)
  }, [])

  useEffect(() => {
    const media = window.matchMedia('(min-width: 1024px)')
    const change = () => setDesktop(media.matches)
    media.addEventListener('change', change)
    return () => media.removeEventListener('change', change)
  }, [])

  useEffect(() => {
    window.addEventListener('online', recheckConnection)
    return () => window.removeEventListener('online', recheckConnection)
  }, [recheckConnection])

  useEffect(() => () => { reuseRequestRef.current += 1 }, [])

  useEffect(() => {
    latestDraftRef.current = draft
    loadedRef.current = loaded
  }, [draft, loaded])
  useEffect(() => () => {
    if (!loadedRef.current) return
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current)
    void saveImageCenterDraft(draftScope, latestDraftRef.current).catch(() => { /* The page is already unmounted. */ })
  }, [draftScope])

  useEffect(() => {
    let active = true
    void loadImageCenterDraft(draftScope).then((stored) => {
      if (!active || stored?.version !== 1) return
      const defaults = createImageCenterDraft(stored.id, stored.outfits[0]?.id ?? createUuid(), stored.updatedAt)
      const restored: ImageCenterDraft = {
        ...stored,
        model: compactImageAssetSet(stored.model),
        outfits: stored.outfits.map((outfit) => ({ ...outfit, ...compactImageAssetSet(outfit) })),
        config: {
          ...defaults.config,
          ...stored.config,
          resolution: stored.config.resolution === '1k' ? '1k' : '2k',
          fusionPrompt: restoreImageDefaultPrompt(stored.config.fusionPrompt, 'fusion'),
          storyboardPrompt: restoreImageDefaultPrompt(stored.config.storyboardPrompt, 'storyboard'),
          modes: normalizeImageGenerationModes(stored.config.modes),
          quality: normalizeImageGenerationQuality(stored.config.quality),
          outputFormat: 'png',
        },
        tasks: stored.tasks.map((task) => ({
          ...task,
          status: (task.status as string) === 'waiting_api' ? 'failed' : task.status,
          batchId: task.batchId ?? null,
          clientBatchId: task.clientBatchId ?? null,
          providerTaskId: task.providerTaskId ?? null,
          providerTaskIds: task.providerTaskIds ?? (task.providerTaskId ? [task.providerTaskId] : []),
          modelId: task.modelId ?? (task.provider === 'gpt' ? 'openai/gpt-image-2/edit' : 'google/nano-banana-2/edit'),
          aspectRatio: task.aspectRatio ?? defaults.config.aspectRatio,
          resolution: task.resolution ?? defaults.config.resolution,
          quality: task.quality ?? defaults.config.quality,
          outputFormat: 'png',
          allowBackInference: task.allowBackInference ?? true,
          storyboardShots: task.storyboardShots ?? parseStoryboardPrompt(task.prompt.storyboard),
          error: (task.status as string) === 'waiting_api' ? '这是一条旧版草稿，没有提交生成。需要使用时请重新生成。' : task.error,
          errorCode: task.errorCode ?? ((task.status as string) === 'waiting_api' ? 'LEGACY_DRAFT_TASK' : null),
          retryable: task.retryable ?? true,
        })),
      }
      // If the browser closed during a request, keep the original identity for
      // reconciliation and never offer its old draft as a fresh paid submission.
      if (restored.pendingSubmission) {
        const intent = restored.pendingSubmission
        restored.tasks = restored.tasks.map(task => task.clientBatchId === intent.clientBatchId && !task.batchId ? {
          ...task, status: 'write_uncertain', retryable: false, errorCode: 'WRITE_UNCERTAIN',
          error: '上次提交尚未确认，请先核对状态。',
        } : task)
        if (restored.id === intent.draftId) {
          const fresh = blankDraft()
          setDraft({ ...activateDraft(restored, fresh, restored.pendingDrafts ?? []), pendingSubmission: null, draftOrder: [...(restored.draftOrder ?? []).filter(id => id !== intent.draftId), fresh.id], activeTab: 'results' })
        } else setDraft({ ...restored, pendingSubmission: null })
      } else setDraft(restored)
    }).catch(() => { if (active) setSaveState('error') }).finally(() => { if (active) setLoaded(true) })
    return () => { active = false }
  }, [draftScope])
  useEffect(() => {
    if (!loaded) return
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current)
    saveTimerRef.current = window.setTimeout(() => { void saveImageCenterDraft(draftScope, draft).then(() => setSaveState('saved')).catch(() => setSaveState('error')) }, 450)
    return () => { if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current) }
  }, [draft, draftScope, loaded])
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    void fetch('/api/v1/system/status', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal }).then((response) => {
      if (!active) return
      const maintenance = response.headers.get('x-libtv-maintenance')?.toLowerCase()
      if (maintenance === 'query-only') {
        setGenerationAvailability('unavailable')
        setGenerationAvailabilityReason('当前处于维护模式，仅支持登录和查询已保存任务，生成服务暂未开放。')
      } else if (!response.ok) {
        setGenerationAvailability('unavailable')
        setGenerationAvailabilityReason('当前无法确认生图服务状态，创建任务已暂停，请稍后再试或联系管理员。')
      } else {
        setGenerationAvailability('available')
        setGenerationAvailabilityReason('')
      }
    }).catch(() => {
      if (!active) return
      setGenerationAvailability('unavailable')
      setGenerationAvailabilityReason('当前无法连接生图服务，创建任务已暂停，请稍后再试或联系管理员。')
    })
    return () => {
      active = false
      controller.abort()
    }
  }, [connectionRevision])

  const hasActiveTasks = draft.tasks.some((task) => !TERMINAL_IMAGE_STATUSES.has(task.status))
  useEffect(() => {
    onActiveTasksChange(loaded && hasActiveTasks)
  }, [hasActiveTasks, loaded, onActiveTasksChange])

  useEffect(() => {
    if (!loaded) return
    const nextStatuses = new Map(draft.tasks.map((task) => [task.id, task.status]))
    if (!notificationBaselineReadyRef.current) {
      previousTaskStatusesRef.current = nextStatuses
      notificationBaselineReadyRef.current = true
      return
    }
    const events = draft.tasks.flatMap((task): ImageCenterNotificationEvent[] => {
      const notification = imageTaskNotificationForTransition(previousTaskStatusesRef.current.get(task.id), task)
      return notification ? [{ taskId: task.id, status: task.status, notification }] : []
    })
    previousTaskStatusesRef.current = nextStatuses
    if (events.length) onNotifications(events)
  }, [draft.tasks, loaded, onNotifications])

  const pollingBatchIds = useMemo(() => Array.from(new Set(draft.tasks
    .filter((task) => task.batchId && !TERMINAL_IMAGE_STATUSES.has(task.status))
    .map((task) => task.batchId!))).join('|'), [draft.tasks])
  useEffect(() => {
    if (!pollingBatchIds) return
    const batchIds = pollingBatchIds.split('|')
    const controller = new AbortController()
    const pending = new Set<string>()
    const pollBatch = async (batchId: string) => {
      if (pending.has(batchId) || controller.signal.aborted) return
      pending.add(batchId)
      try {
        const batch = await getImageGenerationBatch(batchId, controller.signal)
        if (controller.signal.aborted) return
        setDraft((current) => ({
          ...current,
          tasks: mergeBatchIntoTasks(current.tasks, batch),
          updatedAt: new Date().toISOString(),
        }))
        setPollingErrors((current) => {
          if (!(batchId in current)) return current
          const next = { ...current }
          delete next[batchId]
          return next
        })
      } catch (cause) {
        if (!controller.signal.aborted) setPollingErrors((current) => ({
          ...current, [batchId]: errorMessage(cause, '暂时无法读取此批次进度。'),
        }))
      } finally { pending.delete(batchId) }
    }
    const poll = () => batchIds.forEach((batchId) => { void pollBatch(batchId) })
    poll()
    const timer = window.setInterval(poll, 2500)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [pollingBatchIds])

  const updateDraft = (updater: (current: ImageCenterDraft) => ImageCenterDraft) => {
    setSaveState('saving')
    setDraft((current) => ({ ...updater(current), updatedAt: new Date().toISOString() }))
  }
  useEffect(() => {
    if (!active || !openTaskId) return
    setRecordView('tasks')
    const task = latestDraftRef.current.tasks.find((item) => item.id === openTaskId)
    if (task) setRecordBatch(task.clientBatchId || task.batchId || task.id)
    setDraft((current) => current.activeTab === 'results'
      ? current
      : { ...current, activeTab: 'results', updatedAt: new Date().toISOString() })
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(`image-task-${openTaskId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [active, openTaskId])
  const batchGroups = useMemo(() => {
    const groups = new Map<string, ImageGenerationTask[]>()
    for (const task of draft.tasks) {
      const key = task.clientBatchId || task.batchId || task.id
      groups.set(key, [...(groups.get(key) ?? []), task])
    }
    return [...groups.entries()]
  }, [draft.tasks])
  const visibleRecordTasks = recordBatch === null ? [] : batchGroups.find(([key]) => key === recordBatch)?.[1] ?? []
  const summary = useMemo(() => calculateImageTaskSummary(draft), [draft])
  const errors = useMemo(() => validateImageCenterDraft(draft), [draft])
  const generationBlocked = generationAvailability !== 'available' || !siteProfileId
  const generationStatusMessage = !siteProfileId
    ? siteError || (choices ? '请选择商品所属品牌后再生成。' : '正在读取账号品牌。')
    : generationAvailability === 'checking'
    ? '正在检查生图服务状态，请稍候。'
    : generationAvailability === 'available' ? '' : plainText(generationAvailabilityReason, '暂时无法生成图片，请联系管理员检查服务连接。')
  const selectedIds = useMemo(() => new Set(draft.selectedOutputIds), [draft.selectedOutputIds])
  const previewOutput = previewOutputId
    ? draft.tasks.flatMap((task) => task.outputs).find((output) => output.id === previewOutputId) ?? null
    : null
  const compareOutputs = draft.tasks.flatMap((task) => task.outputs)
  const compareSources = Object.fromEntries(draft.tasks.flatMap((task) => task.outputs.map((output) => [
    output.id, `${task.outfitName}${task.sku ? ` · ${task.sku}` : ''} · 任务 ${task.id} · 批次 ${task.batchId || task.clientBatchId || '本地记录'}`,
  ])))
  const galleryView = desktop && recordView === 'gallery'
  const previewSourceImage = (file: File, label: string) => {
    setPreviewOutputId(null)
    setPreviewSource({ file, label })
  }
  useEffect(() => {
    if (!draft.selectedOutputIds.length) setSelectionBarExpanded(false)
  }, [draft.selectedOutputIds.length])
  const activeOutfit = draft.outfits.find((outfit) => outfit.id === activeOutfitId) ?? draft.outfits[0]
  const errorTarget = errors[0]?.includes('模特') ? '#image-model-materials' : errors[0]?.includes('上传') ? '#asset-composer-title' : '#generation-config-title'
  const pendingDrafts = draft.pendingDrafts ?? []
  const allDrafts = [...pendingDrafts, snapshotDraft(draft)].sort((a, b) => (draft.draftOrder ?? []).indexOf(a.id) - (draft.draftOrder ?? []).indexOf(b.id))
  const switchDraft = (id: string) => {
    if (submissionLock.current || id === draft.id) return
    reuseRequestRef.current += 1
    setActiveOutfitId('')
    updateDraft((current) => {
      const next = current.pendingDrafts?.find((item) => item.id === id)
      return next ? {...activateDraft(current, next, [...(current.pendingDrafts ?? []).filter((item) => item.id !== id), snapshotDraft(current)]), draftOrder: allDrafts.map(item => item.id)} : current
    })
  }
  const addDraft = (copy = false) => {
    if (submissionLock.current) return
    reuseRequestRef.current += 1
    const fresh = blankDraft()
    setActiveOutfitId('')
    updateDraft((current) => ({...activateDraft(current, { ...fresh, name: '新批次 ' + (allDrafts.length + 1), ...(copy ? { model: current.model, config: current.config, siteProfileId: current.siteProfileId } : {}) }, [...(current.pendingDrafts ?? []), snapshotDraft(current)]), draftOrder: [...allDrafts.map(item => item.id), fresh.id]}))
  }
  const deleteDraft = (id: string) => {
    if (submissionLock.current || !window.confirm('删除此未提交批次？')) return
    reuseRequestRef.current += 1
    setActiveOutfitId('')
    updateDraft((current) => {
      const rest = (current.pendingDrafts ?? []).filter((item) => item.id !== id)
      if (id !== current.id) return { ...current, pendingDrafts: rest }
      const next = rest[rest.length - 1] ?? blankDraft()
      return {...activateDraft(current, next, rest.slice(0, -1)), draftOrder: [...allDrafts.filter(item => item.id !== id && item.id !== next.id).map(item => item.id), next.id]}
    })
  }
  const renameDraft = (id: string, value: string) => {
    const name = value.trim().slice(0, 100)
    if (submissionLock.current || !name) return
    updateDraft(current => current.id === id
      ? { ...current, name }
      : { ...current, pendingDrafts: (current.pendingDrafts ?? []).map(item => item.id === id ? { ...item, name } : item) })
  }
  const updateOutfit = (changed: OutfitDraft) => updateDraft((current) => ({ ...current, outfits: current.outfits.map((item) => item.id === changed.id ? changed : item) }))
  const addOutfit = () => productInput.current?.click()
  const populatedOutfits = draft.outfits.filter(outfit => getImageAssetSlots(outfit).some(Boolean))
  const addProducts = (files: File[], named: boolean) => {
    if (submissionLock.current) return
    const groups = new Map<string, { slots: Map<number, File>; error: boolean }>()
    const rejected: string[] = []
    for (const [index, file] of files.entries()) {
      if (!isSupportedImageFile(file)) { rejected.push(file.name); continue }
      const stem = file.name.replace(/\.[^.]+$/, '')
      const match = named ? stem.match(/^(.*?)[_\-\s]*(front|back|side|正面|背面|侧面|正|背|侧|参考[123]|ref[123])$/i) : null
      if (named && (!match || !match[1].trim())) { rejected.push(file.name); continue }
      const name = match ? match[1].replace(/[_\-\s]+$/, '').trim() : stem
      const slot = match ? /^(back|背|背面|ref2|参考2)$/i.test(match[2]) ? 1 : /^(side|侧|侧面|ref3|参考3)$/i.test(match[2]) ? 2 : 0 : 0
      const key = named ? name : name + ':' + index
      const group = groups.get(key) ?? { slots: new Map<number, File>(), error: false }
      if (group.slots.has(slot)) { group.error = true; rejected.push(file.name) }
      group.slots.set(slot, file)
      groups.set(key, group)
    }
    const ready = [...groups.entries()].filter(([key, g]) => {
      if (g.error || !g.slots.has(0)) { rejected.push(key); return false }
      return true
    })
    const capacity = MAX_IMAGE_CENTER_OUTFITS - populatedOutfits.length
    const accepted = ready.slice(0, capacity).map(([key, g], i) => {
      const name = named ? key : key.replace(/:\d+$/, '')
      return { ...createOutfit(createUuid(), populatedOutfits.length + i + 1), name, sku: named ? name : '', ...imageAssetSetFromSlots([g.slots.get(0)!, g.slots.get(1) ?? null, g.slots.get(2) ?? null]) }
    })
    if (accepted.length) {
      updateDraft(current => ({ ...current, outfits: [...current.outfits.filter(o => getImageAssetSlots(o).some(Boolean)), ...accepted] }))
      setActiveOutfitId(accepted[accepted.length - 1].id)
      setEditingOutfit(null)
    }
    setNotice([rejected.length ? '未导入：' + rejected.join('、') : '', ready.length > accepted.length ? '本批最多20款，' + (ready.length - accepted.length) + ' 款未导入，请新建批次后添加。' : ''].filter(Boolean).join('；'))
  }
  const removeOutfit = (id: string) => {
    if (submissionLock.current) return
    const rest = draft.outfits.filter(item => item.id !== id)
    setEditingOutfit(null)
    setActiveOutfitId(rest[0]?.id ?? '')
    updateDraft(current => ({ ...current, outfits: rest.length ? rest : [createOutfit(createUuid(), 1)] }))
  }
  const updateConfig = (changes: Partial<ImageCenterDraft['config']>) => updateDraft((current) => ({ ...current, config: { ...current.config, ...changes } }))
  const selectMode = (mode: ImageGenerationMode) => updateConfig({ modes: [mode] })
  const reuseOutput = async (taskId: string, outputId: string) => {
    if (submissionLock.current) return
    const output = draft.tasks.find((task) => task.id === taskId)?.outputs.find((item) => item.id === outputId)
    if (!output) {
      setNotice('未找到这张生图记录，请重新拖拽结果图。')
      return
    }
    const requestId = ++reuseRequestRef.current
    setReusingOutputId(outputId)
    setNotice(`正在载入「${output.label}」…`)
    try {
      const file = await prepareImageOutputReference(output)
      if (requestId !== reuseRequestRef.current) return
      updateDraft((current) => ({ ...current, activeTab: 'create', model: { front: file, back: null, side: null } }))
      setNotice(`已将「${output.label}」载入为模特参考图，服装图片与生成配置已保留。可修改提示词继续编辑，或切换“分镜图生成”。`)
      window.requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
    } catch {
      if (requestId === reuseRequestRef.current) setNotice('结果图载入失败，请检查图片是否可访问后重试，或下载后上传。')
    } finally {
      if (requestId === reuseRequestRef.current) setReusingOutputId(null)
    }
  }
  const dropOutputForCreation = (event: DragEvent) => {
    if (!event.dataTransfer.types.includes(IMAGE_OUTPUT_DRAG_TYPE)) return
    event.preventDefault()
    event.stopPropagation()
    setCreateDropActive(false)
    const source = parseImageOutputDrag(event.dataTransfer.getData(IMAGE_OUTPUT_DRAG_TYPE))
    if (source) void reuseOutput(source.taskId, source.outputId)
    else setNotice('无法识别拖拽内容，请从生图记录重新拖拽结果图。')
  }
  const submitTaskSet = async (tasks: ImageGenerationTask[], expectedGenerationCount: number) => {
    if (!tasks.length || submissionLock.current) return
    const selectedSite = choices ? accountSiteSelection(choices, tasks[0].siteProfileId || siteProfileId) : ''
    if (!selectedSite) { setNotice(siteError || '请选择商品所属品牌后再生成。'); return }
    const clientBatchId = createUuid()
    const identifiedTasks = tasks.map((task) => ({ ...task, siteProfileId: selectedSite, clientBatchId, batchName: draft.name }))
    const { manifest, files } = buildImageGenerationBatchRequest({
      clientBatchId,
      name: draft.name,
      expectedGenerationCount,
      tasks: identifiedTasks,
    })

    submissionLock.current = true
    setSubmitting(true)
    const intent = { draftId: draft.id, clientBatchId }
    const intentDraft: ImageCenterDraft = {
      ...draft, pendingSubmission: intent, tasks: [...identifiedTasks.map(task => ({ ...task, status: 'submitting' as const })), ...draft.tasks],
    }
    updateDraft((current) => ({ ...current, pendingSubmission: intent, tasks: [...identifiedTasks, ...current.tasks] }))
    let paidRequestStarted = false
    try {
      // The intent must be durable before a potentially chargeable request.
      await saveImageCenterDraft(draftScope, intentDraft)
      updateDraft((current) => ({ ...current, tasks: current.tasks.map((task) => identifiedTasks.some((item) => item.id === task.id) ? { ...task, status: 'submitting' } : task) }))
      paidRequestStarted = true
      const batch = await createImageGenerationBatch(manifest, files)
      updateDraft((current) => ({ ...current, tasks: mergeBatchIntoTasks(current.tasks.map((task) => identifiedTasks.some((item) => item.id === task.id) ? { ...task, batchId: batch.id, clientBatchId } : task), batch) }))
      const fresh = blankDraft()
      updateDraft((current) => ({...activateDraft(current, fresh, current.pendingDrafts ?? []), pendingSubmission: null, draftOrder: [...allDrafts.filter(item => item.id !== draft.id).map(item => item.id), fresh.id]}))
      setActiveOutfitId('')
      setRecordBatch(null)
      setNotice('批次已提交到生图记录，正在处理 ' + expectedGenerationCount + ' 张图片。')
    } catch (cause) {
      const uncertain = paidRequestStarted && (!(cause instanceof ApiError) || cause.code === 'REQUEST_TIMEOUT' || cause.code === 'WRITE_UNCERTAIN' || cause.code.startsWith('IDEMPOTENCY_') || cause.status >= 500 || cause.mayHaveBeenAccepted || cause.mayHaveBeenCharged || cause.requiresReconciliation)
      const message = errorMessage(cause, '暂时无法确认是否提交成功，请先核对任务状态，或联系管理员，先不要重新生成。')
      if (isGenerationUnavailableError(cause)) {
        setGenerationAvailability('unavailable')
        setGenerationAvailabilityReason(generationUnavailableMessage(cause))
      }
      updateDraft((current) => ({ ...current, tasks: current.tasks.map((task) => identifiedTasks.some((item) => item.id === task.id) ? {
        ...task,
        clientBatchId,
        status: uncertain ? 'write_uncertain' : 'failed',
        error: message,
        errorCode: cause instanceof ApiError ? cause.code : 'WRITE_UNCERTAIN',
        retryable: uncertain ? false : cause instanceof ApiError ? cause.retryable : false,
        errorDetails: cause instanceof ApiError ? cause.details : null,
      } : task) }))
      if (uncertain) {
        const fresh = blankDraft()
        updateDraft((current) => ({ ...activateDraft(current, fresh, current.pendingDrafts ?? []), pendingSubmission: null, draftOrder: [...allDrafts.filter(item => item.id !== draft.id).map(item => item.id), fresh.id], activeTab: 'results' }))
        setActiveOutfitId('')
        setRecordBatch(null)
      } else updateDraft(current => ({ ...current, pendingSubmission: null }))
      setNotice(message)
    } finally { submissionLock.current = false; setSubmitting(false) }
  }
  const submit = () => {
    if (submissionLock.current) return
    if (generationBlocked) {
      setNotice(generationStatusMessage)
      return
    }
    if (errors.length) { setNotice(errors[0]); return }
    const tasks = buildWaitingTasks(draft, createUuid).map((task) => ({ ...task, siteProfileId }))
    void submitTaskSet(tasks, summary.totalImages)
  }
  const prepareRegeneration = async (source: ImageGenerationTask) => {
    if (submissionLock.current) return
    if (source.status === 'write_uncertain' || (!source.batchId && !TERMINAL_IMAGE_STATUSES.has(source.status))) {
      setNotice('暂时无法确认这组任务是否提交成功，请先核对状态或联系管理员，先不要重新生成。')
      return
    }
    let restored: ImageGenerationTask
    try {
      restored = await resolveImageCenterReferences(source)
    } catch (cause) {
      setNotice(errorMessage(cause, '历史参考图读取失败，请重试或重新上传原始图片。'))
      return
    }
    const outfitId = createUuid()
    setActiveOutfitId(outfitId)
    updateDraft((current) => {
      const next = createImageCenterRegenerationDraft(current, restored, outfitId)
      return { ...next, id: createUuid(), name: source.batchName || source.outfitName, siteProfileId: source.siteProfileId, pendingDrafts: [...(current.pendingDrafts ?? []), snapshotDraft(current)], config: { ...next.config, resolution: next.config.resolution === '1k' ? '1k' : '2k' } }
    })
    setNotice(restored.model.front && restored.product.front
      ? `已载入「${source.outfitName}」的素材与生成配置。`
      : `已载入「${source.outfitName}」的生成配置，请重新上传模特和产品参考图。`)
    window.requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
  }
  const recoverUncertainTask = async (task: ImageGenerationTask) => {
    if (task.status !== 'write_uncertain') return
    if (!task.clientBatchId) {
      setNotice('找不到这次提交的查询记录，请联系管理员核对，先不要重新生成。')
      return
    }
    try {
      const recovery = await querySubmissionRecovery({
        kind: 'image',
        clientId: task.clientBatchId,
        idempotencyKey: task.clientBatchId,
      })
      if (recovery.state === 'submitted' && recovery.resourceId) {
        const batch = await getImageGenerationBatch(recovery.resourceId)
        updateDraft((current) => ({
          ...current,
          tasks: mergeBatchIntoTasks(current.tasks, batch),
          updatedAt: new Date().toISOString(),
        }))
        setNotice('已找回这组任务，正在读取进度，没有重复创建任务。')
        return
      }
      if (recovery.state === 'in_progress') {
        setNotice('上一次提交还在处理中，请稍后再核对，没有重复创建任务。')
        return
      }
      setNotice(recovery.state === 'unknown'
        ? '暂时无法确认这组任务的状态，请联系管理员核对，先不要重新生成。'
        : '暂时找不到这组任务，但仍可能已经扣费。请联系管理员核对，先不要重新生成。')
    } catch (cause) {
      setNotice(errorMessage(cause, '暂时无法核对状态，请稍后再查，先不要重新生成。'))
    }
  }
  const toggleOutput = (id: string) => {
    const owner = draft.tasks.find((task) => task.outputs.some((output) => output.id === id))
    const output = owner?.outputs.find((item) => item.id === id)
    if (!owner || !output) return
    const alreadySelected = selectedIds.has(id)
    const selectedFromOwner = owner?.outputs.filter((output) => selectedIds.has(output.id)).length ?? 0
    const storyboardLimit = getImageCenterVideoStoryboardLimit(owner)
    if (!alreadySelected && selectedFromOwner >= storyboardLimit) {
      setNotice(`每套视频最多选择 ${storyboardLimit} 张分镜，其余名额用于自动携带全部产品参考图。`)
      return
    }
    updateDraft((current) => ({ ...current, selectedOutputIds: current.selectedOutputIds.includes(id) ? current.selectedOutputIds.filter((item) => item !== id) : [...current.selectedOutputIds, id] }))
  }
  const placeOutput = (taskId: string, outputId: string, beforeOutputId?: string) => {
    const owner = draft.tasks.find((task) => task.id === taskId && task.outputs.some((output) => output.id === outputId))
    if (!owner) return
    const alreadySelected = selectedIds.has(outputId)
    const selectedFromOwner = owner.outputs.filter((output) => selectedIds.has(output.id)).length
    const storyboardLimit = getImageCenterVideoStoryboardLimit(owner)
    if (!alreadySelected && selectedFromOwner >= storyboardLimit) {
      setNotice(`每套视频最多选择 ${storyboardLimit} 张分镜，其余名额用于自动携带全部产品参考图。`)
      return
    }
    updateDraft((current) => {
      const nextIds = current.selectedOutputIds.filter((id) => id !== outputId)
      const targetIndex = beforeOutputId ? nextIds.indexOf(beforeOutputId) : -1
      if (targetIndex >= 0) nextIds.splice(targetIndex, 0, outputId)
      else {
        const currentOwner = current.tasks.find((task) => task.id === taskId)
        const ownerIds = new Set(currentOwner?.outputs.map((output) => output.id) ?? [])
        let insertionIndex = nextIds.length
        for (let index = nextIds.length - 1; index >= 0; index -= 1) {
          if (ownerIds.has(nextIds[index])) { insertionIndex = index + 1; break }
        }
        nextIds.splice(insertionIndex, 0, outputId)
      }
      return { ...current, selectedOutputIds: nextIds }
    })
  }
  const prepareVideos = async () => {
    if (preparingVideos || !canMakeVideos) return
    const selectedTasks = getOrderedSelectedTasks(draft.tasks, draft.selectedOutputIds)
    if (!selectedTasks.length) return
    const oversized = selectedTasks.find(({ task, outputs }) => outputs.length > getImageCenterVideoStoryboardLimit(task))
    if (oversized) {
      setNotice(`“${oversized.task.outfitName}”选择的分镜超过 ${getImageCenterVideoStoryboardLimit(oversized.task)} 张，请减少分镜后再送入视频队列。`)
      return
    }
    const preparedOutputIds = new Set(selectedTasks.flatMap(({ outputs }) => outputs.map((output) => output.id)))
    setPreparingVideos(true)
    try {
      await onPrepareVideos(
        selectedTasks.map(({ task, outputs }) => ({ task, outputs })),
        () => updateDraft((current) => ({
          ...current,
          selectedOutputIds: current.selectedOutputIds.filter((id) => !preparedOutputIds.has(id)),
        })),
      )
    } catch (cause) {
      setNotice(errorMessage(cause, '无法准备视频制作素材。'))
    } finally { setPreparingVideos(false) }
  }
  if (!loaded) return <div className="image-center-loading" hidden={!active}><LoaderCircle className="spin" size={24} /><strong>正在恢复图片批次</strong></div>

  return <div className="image-center page-view" hidden={!active} onDragEnd={() => setCreateDropActive(false)}>
    <header className="image-center-title"><h1>图片中心</h1><div className="image-center-title__actions"><span className={`image-center-save-state is-${saveState}`}><Save size={14} />{saveState === 'saved' ? '草稿已保存' : saveState === 'saving' ? '正在保存' : '草稿保存失败'}</span></div></header>
    <nav className="image-center-tabs" aria-label="图片中心视图" inert={submitting}><button
      type="button"
      className={`${draft.activeTab === 'create' ? 'is-active' : ''}${createDropActive ? ' is-output-drop-target' : ''}`}
      onClick={() => updateDraft((current) => ({ ...current, activeTab: 'create' }))}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes(IMAGE_OUTPUT_DRAG_TYPE)) return
        event.preventDefault()
        event.dataTransfer.dropEffect = 'copy'
        setCreateDropActive(true)
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setCreateDropActive(false)
      }}
      onDrop={dropOutputForCreation}
      title="将生图记录中的结果图拖到这里，继续编辑或生成分镜图"
    ><Layers3 size={17} />{createDropActive ? '松开继续创作' : '批量创作'}</button><button type="button" className={draft.activeTab === 'results' ? 'is-active' : ''} onClick={() => { setRecordBatch(null); updateDraft((current) => ({ ...current, activeTab: 'results' })) }}><Images size={17} />生图记录{batchGroups.length ? <i>{batchGroups.length}</i> : null}</button></nav>
    {notice ? <div className={`image-center-notice${errors.includes(notice) ? ' is-danger' : ''}`} role="status"><AlertTriangle size={16} /><span>{notice}</span><button type="button" onClick={() => setNotice('')} aria-label="关闭提示"><X size={15} /></button></div> : null}
    {generationAvailability !== 'available' ? <div className="image-center-notice" role="status">
      {generationAvailability === 'checking' ? <LoaderCircle size={16} className="spin" /> : <AlertTriangle size={16} />}
      <span>{generationAvailability === 'checking' ? '正在只读检查生图服务连接…' : plainText(generationAvailabilityReason)} </span>
      <button type="button" className="secondary-button" disabled={generationAvailability === 'checking'} onClick={recheckConnection}>{generationAvailability === 'checking' ? '正在检查连接' : '重新检查连接'}</button>
    </div> : null}

    {draft.activeTab === 'create' ? <>
      <div className="v9-draft-toolbar">
        <div className="v9-draft-tabs" role="tablist" aria-label="待提交批次">
          {allDrafts.map((item) => <div key={item.id} className={'v9-draft-tab' + (item.id === draft.id ? ' is-current' : '')}>
            <DraftTabName item={item} selected={item.id === draft.id} disabled={submitting} onSelect={() => switchDraft(item.id)} onRename={name => renameDraft(item.id, name)} />
            <button type="button" aria-label={'删除草稿 ' + item.name} disabled={submitting} onClick={() => deleteDraft(item.id)}><X size={13}/></button>
          </div>)}
          <button type="button" className="v9-new-draft" disabled={submitting} onClick={() => addDraft()}><Plus size={16}/>新建批次</button>
        </div>
        <button type="button" className="text-button" disabled={submitting} onClick={() => addDraft(true)}>复制配置</button>
      </div>
      <div className="image-center-create v9-image-create" inert={submitting}>
      <div className="image-center-materials">
      <section className="image-center-section asset-composer" aria-labelledby="asset-composer-title">
        <div className="creation-model-materials" id="image-model-materials">
          <OrderedAssetUpload idPrefix={'image-center-model-' + draft.id} title="共用模特参考" onPreview={previewSourceImage} assets={draft.model} onChange={(model) => updateDraft((current) => ({ ...current, model }))} />
        </div>
        <div className="image-center-section__heading"><div><h2 id="asset-composer-title">商品素材 <span className="creation-count">{populatedOutfits.length}/{MAX_IMAGE_CENTER_OUTFITS}</span></h2></div><div className="creation-material-actions"><button type="button" className="text-button" onClick={() => namedProductInput.current?.click()}><Images size={15}/>按命名批量导入</button><input ref={namedProductInput} hidden type="file" multiple accept={IMAGE_FILE_ACCEPT} onChange={event => {addProducts(Array.from(event.currentTarget.files ?? []), true); event.currentTarget.value = ''}}/><input ref={productInput} hidden type="file" multiple accept={IMAGE_FILE_ACCEPT} onChange={event => {addProducts(Array.from(event.currentTarget.files ?? []), false); event.currentTarget.value = ''}}/></div></div>
        <div className="v9-product-grid">
          {populatedOutfits.map((outfit, index) => <button key={outfit.id} type="button" className={'v9-product-card'+(outfit.id === activeOutfit.id ? ' is-current' : '')} aria-pressed={outfit.id === activeOutfit.id} onClick={() => {setActiveOutfitId(outfit.id); setEditingOutfit(outfit.id)}}>
            <span className="v9-product-media"><AssetPreview file={outfit.front} alt={productLabel(outfit, index)} />{outfit.id === activeOutfit.id ? <i className="creation-selected-mark"><Check size={12}/></i> : null}</span><strong>{productLabel(outfit, index)}</strong><small>{getImageAssetSlots(outfit).filter(Boolean).length}/3 张参考图</small>
          </button>)}
          <button type="button" className="v9-product-card v9-add-product" disabled={populatedOutfits.length >= MAX_IMAGE_CENTER_OUTFITS} onClick={addOutfit}><span className="creation-add-icon"><Plus size={22}/></span>上传商品</button>
        </div>
        {editingOutfit && activeOutfit.id === editingOutfit ? <div id="active-outfit-editor" className="active-outfit-editor creation-product-edit"><div className="creation-edit-head"><strong>{productLabel(activeOutfit, draft.outfits.indexOf(activeOutfit))}</strong><button className="text-button" type="button" onClick={() => removeOutfit(activeOutfit.id)}>删除商品</button><button className="text-button" type="button" aria-label="关闭参考图管理" onClick={() => setEditingOutfit(null)}><X size={16}/></button></div><div className="creation-product-meta"><label>商品名称<input value={activeOutfit.name} onChange={e => updateOutfit({...activeOutfit, name:e.target.value})}/></label><label>SKU<input value={activeOutfit.sku} onChange={e => updateOutfit({...activeOutfit, sku:e.target.value})}/></label></div><OutfitCard outfit={activeOutfit} label={productLabel(activeOutfit, draft.outfits.indexOf(activeOutfit))} onChange={updateOutfit}/></div> : null}
      </section>


      </div>
      <section className="image-center-section image-brief-section" aria-labelledby="image-settings-title">
        <div className="image-center-section__heading"><h2 id="image-settings-title">生成配置</h2></div>
        <AccountSiteField compact value={siteProfileId} onChange={(siteProfileId) => updateDraft((current) => ({ ...current, siteProfileId }))} choices={choices} error={siteError} disabled={submitting} />
      <section className="image-center-section generation-config" aria-labelledby="generation-config-title">
        <div className="image-center-section__heading"><div><h2 id="generation-config-title">生图方式</h2></div></div>
        <div className="image-prompt-grid" role="radiogroup" aria-label="生成任务">
          <section className={draft.config.modes.includes('fusion') ? 'is-selected' : 'is-disabled'}><header><button type="button" className="image-prompt-mode" role="radio" aria-checked={draft.config.modes.includes('fusion')} onClick={() => selectMode('fusion')}><span><Sparkles size={17} /></span><span><strong>换装图片</strong><small>让模特穿上你的服装，生成展示图</small></span><i>{draft.config.modes.includes('fusion') ? <Check size={14} /> : null}</i></button></header></section>
          <section className={draft.config.modes.includes('storyboard') ? 'is-selected' : 'is-disabled'}><header><button type="button" className="image-prompt-mode" role="radio" aria-checked={draft.config.modes.includes('storyboard')} onClick={() => selectMode('storyboard')}><span><Images size={17} /></span><span><strong>视频分镜</strong><small>生成用于制作服装视频的镜头画面</small></span><i>{draft.config.modes.includes('storyboard') ? <Check size={14} /> : null}</i></button></header></section>
        </div>
      </section>
        <fieldset className="image-config-grid image-basic-settings" aria-label="图片生成设置" disabled={submitting}>
          <fieldset className="image-config-block image-aspect-settings"><legend>画面比例</legend><div className="image-aspect-options">{['9:16', '16:9', '1:1', '3:4', '4:3'].map((ratio) => <button key={ratio} type="button" aria-pressed={draft.config.aspectRatio === ratio} onClick={() => updateConfig({ aspectRatio: ratio })}>{ratio}</button>)}</div></fieldset>
          <fieldset className="image-config-block image-config-select"><legend>分辨率</legend><select aria-label="分辨率" value={draft.config.resolution} onChange={(event) => updateConfig({ resolution: event.target.value })}><option value="1k">1K</option><option value="2k">2K</option></select></fieldset>
          <fieldset className="image-config-block image-config-select"><legend>每款出图</legend><select aria-label="统一结果数量" value={draft.config.resultCount} onChange={(event) => updateConfig({ resultCount: Number(event.target.value) as 1 | 2 | 3 | 4 })}>{[1, 2, 3, 4].map((count) => <option key={count} value={count}>{count} 张</option>)}</select></fieldset>
        </fieldset>
        <GenerationBrief value={draft.config.creativeBrief ?? ''} onChange={(creativeBrief) => updateConfig({ creativeBrief })} disabled={submitting} placeholder="选填" />
      </section>
      <footer className="image-center-command-bar">
      <div><strong>{summary.outfitCount}/{populatedOutfits.length} 款已就绪</strong><span>预计生成 {summary.totalImages} 张图片</span></div>
      {errors.length ? <a id="image-generation-availability" href={errorTarget}><AlertTriangle size={15} />{errors[0]}</a> : generationBlocked ? <span id="image-generation-availability" title={generationStatusMessage}><AlertTriangle size={15} />{generationStatusMessage}</span> : <span id="image-generation-availability" className="is-ready"><CheckCircle2 size={15} />配置完整</span>}
      <button type="button" className="primary-button generation-priced-button" disabled={submitting || generationBlocked || errors.length > 0} onClick={submit} title={generationBlocked ? generationStatusMessage : undefined} aria-describedby="image-generation-availability">{submitting ? <LoaderCircle className="spin" size={17} /> : <WandSparkles size={17} />}<span>{submitting ? '正在提交' : generationBlocked ? '开始生成' : '生成 ' + summary.totalImages + ' 张'}</span><ArrowRight size={15} aria-hidden="true" /><GenerationCostBadge category="imagePriceUsd" units={summary.totalImages} /></button>
      </footer>
    </div></> : <section className="image-results" aria-labelledby="image-results-title">
      <div className="image-results-heading">
        <div><h2 id="image-results-title">{recordBatch ? '批次结果' : '生图记录'}</h2>{recordBatch ? <button type="button" className="text-button" onClick={() => setRecordBatch(null)}>返回批次列表</button> : null}</div>
        <div className="image-results-heading__actions">
          {desktop && recordBatch ? <div className="image-record-view" role="group" aria-label="生图记录显示方式">
            <button type="button" className="secondary-button" aria-pressed={recordView === 'gallery'} onClick={() => setRecordView('gallery')}><Images size={16} />紧凑画廊</button>
            <button type="button" className="secondary-button" aria-pressed={recordView === 'tasks'} onClick={() => setRecordView('tasks')}><Layers3 size={16} />任务视图</button>
          </div> : null}
          <button type="button" className="secondary-button" disabled={history.loading} onClick={history.refresh}><RotateCcw size={16} />刷新历史</button>
          {compareOutputs.length >= 2 ? <button type="button" className="secondary-button" onClick={() => setCompareOpen(true)}><Columns2 size={16} />并排比较</button> : null}
          <button type="button" className="secondary-button" onClick={() => updateDraft((current) => ({ ...current, activeTab: 'create' }))}><Plus size={16} />继续创建</button>
        </div>
      </div>
      {history.loading ? <p role="status"><LoaderCircle size={16} className="spin" />正在同步生图历史…</p> : null}
      {history.error ? <div className="image-center-notice is-danger" role="alert"><AlertTriangle size={16} /><span>{history.error}</span><button type="button" className="secondary-button" disabled={history.loading} onClick={history.refresh}>重试同步</button></div> : null}
      {Object.entries(pollingErrors).filter(([batchId]) => draft.tasks.some((task) => task.batchId === batchId && !TERMINAL_IMAGE_STATUSES.has(task.status))).map(([batchId, message]) => <div className="image-center-notice is-danger" role="status" key={batchId}>
        <AlertTriangle size={16} /><span>批次 {batchId}（{[...new Set(draft.tasks.filter((task) => task.batchId === batchId).map((task) => task.outfitName))].join('、')}）进度查询失败：{message} 将自动重新查询；已有结果已保留，不会重新生成。</span>
      </div>)}
      {draft.tasks.filter((task) => task.status === 'write_uncertain').map((task) => <div className="image-center-notice is-danger" key={`recovery-${task.id}`} role="status"><AlertTriangle size={16} /><span>{task.outfitName} 是否提交成功还不确定，请先核对状态。</span><button type="button" className="secondary-button" style={{ width: 'auto', height: 'auto', display: 'inline-flex', whiteSpace: 'nowrap' }} onClick={() => void recoverUncertainTask(task)}><RotateCcw size={15} />核对状态</button></div>)}
      {recordBatch === null && batchGroups.length > 0 ? <div className="v9-record-list">
        <div className="v9-record-controls"><input value={recordSearch} onChange={(event) => setRecordSearch(event.target.value)} placeholder="搜索批次 / SKU" aria-label="搜索批次"/><select value={recordFilter} onChange={(event) => setRecordFilter(event.target.value)} aria-label="批次状态"><option value="all">全部状态</option><option value="running">生成中</option><option value="completed">已完成</option><option value="attention">需处理</option></select></div>
        {batchGroups.map(([key, tasks]) => {
          const done = tasks.filter((task) => task.status === 'completed').length
          const running = tasks.some((task) => !TERMINAL_IMAGE_STATUSES.has(task.status))
          const status = running ? 'running' : done === tasks.length ? 'completed' : 'attention'
          const name = tasks[0].batchName || tasks[0].outfitName
          if ((recordFilter !== 'all' && recordFilter !== status) || ![name, ...tasks.flatMap((task) => [task.sku, task.outfitName])].join(' ').toLowerCase().includes(recordSearch.toLowerCase())) return null
          return <button type="button" className="v9-record-row" key={key} onClick={() => setRecordBatch(key)}>
            <span className="v9-record-thumbs">{tasks.slice(0, 3).map((task) => <span key={task.id}><AuthenticatedAssetPreview file={task.product.front} url={task.outputs[0]?.url ?? ''} alt={task.outfitName}/></span>)}</span>
            <span><strong>{name}</strong><small>{tasks[0].siteProfileId || ''} · {new Date(tasks[0].createdAt).toLocaleString('zh-CN')}</small></span>
            <span>{tasks.length} 款 · {tasks.reduce((n, task) => n + task.outputs.length, 0)} 张</span>
            <span><progress value={done} max={tasks.length}/><small>{done}/{tasks.length} 款完成</small></span>
            <span>{status === 'running' ? '生成中' : status === 'completed' ? '已完成' : '需处理'} <ArrowRight size={15}/></span>
          </button>
        })}
      </div> : null}
      {!draft.tasks.length && !history.loading && !history.error ? <div className="image-results-empty"><span><Images size={28} /></span><h3>还没有生图记录</h3><button type="button" className="primary-button" onClick={() => updateDraft((current) => ({ ...current, activeTab: 'create' }))}>开始配置</button></div> : <div className={galleryView ? 'image-task-list is-gallery' : 'image-task-list'}>{visibleRecordTasks.map((task) => {
        const status = imageStatusPresentation(task.status)
        const hasReferences = [...Object.values(task.model), ...Object.values(task.product)].some(Boolean)
        const active = ['uploading', 'submitting', 'queued', 'running'].includes(task.status)
        if (galleryView && task.outputs.length) return <div className="image-gallery-group" key={task.id}>{task.outputs.map((output) => <article key={output.id} className="image-gallery-item" aria-label={`${task.outfitName} · ${output.label}`}>
          <OutputCard taskId={task.id} output={output} selected={selectedIds.has(output.id)} onReuse={() => void reuseOutput(task.id, output.id)} reusing={reusingOutputId === output.id} onToggle={() => toggleOutput(output.id)} onPreview={() => { setPreviewSource(null); setPreviewOutputId(output.id) }} />
          <div className="image-gallery-source">
            <strong title={task.outfitName}>{task.outfitName}</strong>
            <span className={`image-gallery-status is-${status.tone}`}>{status.label}{task.sku ? ` · ${task.sku}` : ''}</span>
            <button type="button" className="secondary-button" onClick={() => void prepareRegeneration(task)} title="使用相同素材和配置重新创作"><RotateCcw size={14} />重新配置</button>
          </div>
          <ImageTaskDetails task={task}><button type="button" className="secondary-button" onClick={() => {
            setRecordView('tasks')
            window.requestAnimationFrame(() => document.getElementById(`image-task-${task.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
          }}>查看所属任务与参考图</button></ImageTaskDetails>
        </article>)}</div>
        return <article id={`image-task-${task.id}`} key={task.id} className="image-task-card"><header><div className="image-task-title"><span><Package size={18} /></span><div><strong>{task.outfitName}</strong><small>{task.sku || new Date(task.createdAt).toLocaleString('zh-CN')}</small></div></div><div className="image-task-header-actions"><div className="image-task-meta"><span>{imageModelLabel(task.provider, task.modelId)}</span><span>{task.modes.map((mode) => mode === 'fusion' ? '多图融合' : '分镜图').join(' + ')}</span><i className={`is-${status.tone}`}>{active ? <LoaderCircle className="spin" size={13} /> : task.status === 'completed' ? <CheckCircle2 size={13} /> : <Clock3 size={13} />}{status.label}</i></div><button type="button" className="secondary-button image-task-regenerate" onClick={() => void prepareRegeneration(task)} title="使用相同素材和配置重新创作"><RotateCcw size={15} />重新生成</button></div></header><div className="image-task-body" style={hasReferences ? undefined : { gridTemplateColumns: 'minmax(0, 1fr)' }}>{hasReferences ? <div className="image-task-sources"><SourceThumb file={task.model.front} label="模特参考图" onPreview={previewSourceImage} /><SourceThumb file={task.product.front} label="服装参考图" onPreview={previewSourceImage} /><SourceThumb file={task.model.back} label="模特参考图" onPreview={previewSourceImage} /><SourceThumb file={task.product.back} label="服装参考图" onPreview={previewSourceImage} /><SourceThumb file={task.model.side} label="模特参考图" onPreview={previewSourceImage} /><SourceThumb file={task.product.side} label="服装参考图" onPreview={previewSourceImage} /></div> : null}{task.outputs.length ? <div className="image-output-grid">{task.outputs.map((output) => <OutputCard key={output.id} taskId={task.id} output={output} selected={selectedIds.has(output.id)} onReuse={() => void reuseOutput(task.id, output.id)} reusing={reusingOutputId === output.id} onToggle={() => void toggleOutput(output.id)} onPreview={() => { setPreviewSource(null); setPreviewOutputId(output.id) }} />)}</div> : <div className={`image-task-waiting${task.error ? ' is-error' : ''}`}><span>{active ? <LoaderCircle className="spin" size={20} /> : <Clock3 size={20} />}</span><div><strong>{task.error ? userFacingError({ code: task.errorCode, message: task.error }) : status.label}</strong><p>{task.error ? (task.retryable === false ? '请先核对任务和扣费情况，或联系管理员，确认前不要重新生成。' : '请根据上方提示检查素材和设置，再决定是否重新生成。') : task.status === 'queued' ? '任务已进入 WaveSpeed 队列。' : task.status === 'running' ? 'WaveSpeed 正在生成图片，页面会自动刷新结果。' : '正在上传参考图并提交生成。'}</p></div></div>}</div><ImageTaskDetails task={task} /></article>
      })}</div>}
      {history.hasMore ? <button type="button" className="secondary-button" disabled={history.loading} onClick={history.loadMore}>{history.loading ? '正在读取…' : '加载更多生图记录'}</button> : null}
    </section>}
    {canMakeVideos && active && draft.activeTab === 'results' && selectedIds.size ? createPortal(<SelectionWorkbench
      tasks={draft.tasks}
      selectedOutputIds={draft.selectedOutputIds}
      expanded={selectionBarExpanded}
      preparing={preparingVideos}
      onExpandedChange={setSelectionBarExpanded}
      onClear={() => updateDraft((current) => ({ ...current, selectedOutputIds: [] }))}
      onRemove={(outputId) => updateDraft((current) => ({ ...current, selectedOutputIds: current.selectedOutputIds.filter((id) => id !== outputId) }))}
      onPlaceOutput={placeOutput}
      onGenerate={() => void prepareVideos()}
    />, document.body) : null}
    {active && previewOutput ? <OutputPreview
      output={previewOutput}
      selected={selectedIds.has(previewOutput.id)}
      onToggle={() => toggleOutput(previewOutput.id)}
      onClose={() => setPreviewOutputId(null)}
    /> : null}
    {active && previewSource ? <AssetPreviewDialog
      file={previewSource.file}
      label={previewSource.label}
      onClose={() => setPreviewSource(null)}
    /> : null}
    {active && compareOpen ? <ImageOutputCompare outputs={compareOutputs} initialIds={draft.selectedOutputIds.slice(0, 2)} sources={compareSources} onClose={() => setCompareOpen(false)} /> : null}
  </div>
}
