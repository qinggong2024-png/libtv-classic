import {
  AlertTriangle,
  History,
  LoaderCircle,
  PackageOpen,
  Plus,
  UploadCloud,
  X,
} from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'

import {
  KLING_MAX_REFERENCES,
  SUBJECT_IMAGE_ACCEPT,
  SUBJECT_MAX_DETAIL_IMAGES,
  createEmptySubjectReferenceFiles,
  buildSubjectImageReferences,
  getSubjectFileCount,
  getSubjectReferenceCount,
  validateReferenceBudget,
} from './subjectFiles'
import type { SubjectInputMode, SubjectReferenceFiles } from './subjectFiles'
import { fillSubjectDropSlots, isSubjectPanelDropExcluded } from './subjectPanelDrop'
import { getKlingElementStatusLabel, isKlingElementAvailable } from './klingElementStatus'
import { swapSubjectReferenceFiles } from './subjectUploadReorder'
import type { SubjectDragSlot } from './subjectUploadReorder'
import type { KlingElement } from './types'
import { UploadStatus, type FileUploadState } from './shared/components/UploadStatus'

const COLLAPSED_HISTORY_COUNT = 6
const SUBJECT_DRAG_MIME = 'application/x-subject-slot'

function readSubjectDragSlot(dataTransfer: DataTransfer): SubjectDragSlot | null {
  const value = dataTransfer.getData(SUBJECT_DRAG_MIME) || dataTransfer.getData('text/plain')
  if (!value) return null
  try {
    const parsed = JSON.parse(value) as SubjectDragSlot
    if (!parsed || !['frontalImage', 'backImage', 'detailImages'].includes(parsed.slot)) return null
    return parsed
  } catch {
    return null
  }
}

export type SubjectHistoryElement = Pick<
  KlingElement,
  'id' | 'name' | 'description' | 'status' | 'imageUrls' | 'raw'
>

export interface SubjectInputPanelProps {
  mode: SubjectInputMode
  onModeChange: (mode: SubjectInputMode) => void
  uploadedFiles: SubjectReferenceFiles
  onUploadedFilesChange: (files: SubjectReferenceFiles) => void
  elements: readonly SubjectHistoryElement[]
  selectedElementId: string
  onSelectedElementIdChange: (elementId: string) => void
  storyboardReferenceCount: number
  referenceLimit?: number
  onOpenProductHistory?: () => void
  allowHistorySelection?: boolean
  disabled?: boolean
  elementsLoading?: boolean
  error?: string | null
  elementsError?: string | null
  onReloadElements?: () => void
  onLoadMoreElements?: () => void
  hasMoreElements?: boolean
  loadingMoreElements?: boolean
  className?: string
  uploadStates?: ReadonlyMap<File, FileUploadState>
}

function isUsableElement(element: SubjectHistoryElement): boolean {
  return !element.raw?.localJob && isKlingElementAvailable(element.status)
}

function LocalFileThumbnail({ file, alt }: { file: File; alt: string }) {
  const [preview, setPreview] = useState<{ file: File; src: string } | null>(null)

  useEffect(() => {
    let active = true
    const objectUrl = URL.createObjectURL(file)
    queueMicrotask(() => {
      if (active) setPreview({ file, src: objectUrl })
    })
    return () => {
      active = false
      URL.revokeObjectURL(objectUrl)
    }
  }, [file])

  return preview?.file === file
    ? <img src={preview.src} alt={alt} draggable={false} />
    : <span className="image-skeleton" aria-label="图片加载中" />
}

export function SubjectInputPanel({
  mode,
  onModeChange,
  uploadedFiles,
  onUploadedFilesChange,
  elements,
  selectedElementId,
  onSelectedElementIdChange,
  storyboardReferenceCount,
  referenceLimit = KLING_MAX_REFERENCES,
  onOpenProductHistory,
  allowHistorySelection = true,
  disabled = false,
  elementsLoading = false,
  error = null,
  elementsError = null,
  onReloadElements,
  onLoadMoreElements,
  hasMoreElements = false,
  loadingMoreElements = false,
  className = '',
  uploadStates,
}: SubjectInputPanelProps) {
  const id = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const references = buildSubjectImageReferences(uploadedFiles)
  const [selectionError, setSelectionError] = useState('')
  const [showAllHistory, setShowAllHistory] = useState(false)
  const subjectReferenceCount = getSubjectReferenceCount(mode, uploadedFiles, selectedElementId)
  const budget = validateReferenceBudget(storyboardReferenceCount, subjectReferenceCount, referenceLimit)
  const selectedElement = elements.find((element) => element.id === selectedElementId)
  const usableElements = elements.filter(isUsableElement)
  const visibleElements = showAllHistory
    ? usableElements
    : usableElements.slice(0, COLLAPSED_HISTORY_COUNT)
  const historySelectionError = mode === 'history' && selectedElementId
    ? !selectedElement
      ? '所选历史主体已不可用，请重新选择。'
      : !isUsableElement(selectedElement)
        ? `“${selectedElement.name || selectedElement.id}”当前${getKlingElementStatusLabel(selectedElement.status)}，暂不能用于生成。`
        : ''
    : ''
  const panelErrors = [
    selectionError,
    error ?? '',
    budget.error,
    mode === 'history' && !allowHistorySelection ? '当前视频模型不支持历史 Element 主体，请改用上传参考图。' : '',
    historySelectionError,
  ].filter(Boolean)
  const uploadHasFiles = getSubjectFileCount(uploadedFiles) > 0
  const panelClassName = [
    'subject-input-panel',
    className,
  ].filter(Boolean).join(' ')

  const clearUploadedFiles = () => {
    if (uploadHasFiles) onUploadedFilesChange(createEmptySubjectReferenceFiles())
  }

  const changeMode = (nextMode: SubjectInputMode) => {
    if (disabled || (nextMode === 'history' && !allowHistorySelection) || nextMode === mode) return
    setSelectionError('')
    setShowAllHistory(false)
    if (nextMode !== 'upload') clearUploadedFiles()
    if (nextMode !== 'history' && selectedElementId) onSelectedElementIdChange('')
    onModeChange(nextMode)
  }

  const clearSubject = () => {
    if (disabled) return
    setSelectionError('')
    setShowAllHistory(false)
    clearUploadedFiles()
    if (selectedElementId) onSelectedElementIdChange('')
    if (mode !== 'none') onModeChange('none')
  }

  const selectPanelDropFiles = (chosen: File[]) => {
    if (!chosen.length) return
    const result = fillSubjectDropSlots(uploadedFiles, chosen)
    if (!result.acceptedCount) {
      setSelectionError('主体参考图已满，请先移除图片再添加。')
      return
    }
    if (mode !== 'upload') changeMode('upload')
    setSelectionError(result.acceptedCount < chosen.length
      ? `主体参考图最多上传 ${2 + SUBJECT_MAX_DETAIL_IMAGES} 张，已保留本次添加的前 ${result.acceptedCount} 张。`
      : '')
    onUploadedFilesChange(result.files)
  }

  const selectHistoryElement = (elementId: string) => {
    setSelectionError('')
    onSelectedElementIdChange(elementId)
  }

  const reorderSubjectFiles = (source: SubjectDragSlot, target: SubjectDragSlot) => {
    if (disabled || (source.slot === target.slot && source.index === target.index)) return
    const next = swapSubjectReferenceFiles(uploadedFiles, source, target)
    if (next === uploadedFiles) return
    setSelectionError('')
    if (mode !== 'upload') changeMode('upload')
    onUploadedFilesChange(next)
  }

  const uploadButton = <button type="button" className="upload-zone subject-unified-upload" disabled={disabled || references.length >= 2 + SUBJECT_MAX_DETAIL_IMAGES} onClick={() => inputRef.current?.click()} onDragOver={(event) => {
        event.preventDefault()
        event.stopPropagation()
        event.dataTransfer.dropEffect = disabled ? 'none' : 'copy'
      }} onDrop={(event) => {
        event.preventDefault()
        event.stopPropagation()
        if (!disabled) selectPanelDropFiles(Array.from(event.dataTransfer.files))
      }}>
        <span className="upload-icon">{className.includes("v6-subject") ? <Plus size={22}/> : <UploadCloud size={22} />}</span>
        <span><strong>{references.length ? (className.includes('v6-subject') ? '添加' : '继续添加参考图') : className.includes('v6-subject') ? '点击或拖入' : '拖拽或点击上传参考图'}</strong><small>第一张为主参考，其余补充细节</small></span>
      </button>
  const errorId = `${id}-error`
  return <section
    className={panelClassName}
    aria-labelledby={`${id}-title`}
    aria-describedby={panelErrors.length ? errorId : undefined}
    aria-disabled={disabled || undefined}
    onDragOver={(event) => {
      if (!Array.from(event.dataTransfer.types).includes('Files')) return
      event.preventDefault()
      event.dataTransfer.dropEffect = disabled || isSubjectPanelDropExcluded(event.target)
        ? 'none'
        : 'copy'
    }}
    onDrop={(event) => {
      if (!Array.from(event.dataTransfer.types).includes('Files')) return
      event.preventDefault()
      if (disabled || isSubjectPanelDropExcluded(event.target)) return
      selectPanelDropFiles(Array.from(event.dataTransfer.files))
    }}
  >
    <header className="subject-input-panel__header">
      <div>
        <h2 id={`${id}-title`}>{className.includes("v6-subject") ? "商品 / 人物参考图" : "人物 / 商品参考图（选填）"}</h2>
        <p>让人物长相或商品外观更一致</p>
      </div>
      <div className="subject-input-panel__header-actions" role="group" aria-label="参考图来源">
        <button
          type="button"
          className={mode === 'history' ? 'is-selected' : ''}
          disabled={disabled || !allowHistorySelection}
          title={!allowHistorySelection ? (onOpenProductHistory ? '请先选择品牌，并留出参考图数量' : '当前 CLI 模型不支持历史 Element 主体') : undefined}
          aria-pressed={mode === 'history'}
          onClick={() => onOpenProductHistory ? onOpenProductHistory() : changeMode(mode === 'history' ? 'none' : 'history')}
        >
          <History aria-hidden="true" size={15} />
          {onOpenProductHistory ? '历史商品图' : '从历史选择'}
        </button>
        {mode !== 'none' ? <button
          type="button"
          className="subject-input-panel__clear"
          disabled={disabled}
          onClick={clearSubject}
          aria-label="清除主体"
        >
          <X aria-hidden="true" size={15} />
        </button> : null}
      </div>
    </header>

    {mode !== 'history' ? <div className="subject-input-panel__upload">
      <input ref={inputRef} id={`${id}-images`} hidden type="file" multiple accept={SUBJECT_IMAGE_ACCEPT} disabled={disabled} aria-label="人物或商品参考图" onChange={(event) => {
        selectPanelDropFiles(Array.from(event.currentTarget.files ?? []))
        event.currentTarget.value = ''
      }} />
      {(!className.includes("v6-subject") || !references.length) ? uploadButton : null}
      {references.length ? <div className="subject-reference-previews" role="list" aria-label="已上传的人物或商品参考图">
        {references.map((reference, index) => {
          const slot: SubjectDragSlot = reference.angle === 'FRONT' ? { slot: 'frontalImage' } : reference.angle === 'BACK' ? { slot: 'backImage' } : { slot: 'detailImages', index: uploadedFiles.detailImages.indexOf(reference.file) }
          const label = index === 0 ? '主参考图' : `补充参考图 ${index}`
          return <div className="subject-reference-preview" role="listitem" key={`${reference.file.name}-${reference.file.size}-${reference.file.lastModified}-${index}`} draggable={!disabled} title="拖动调整参考顺序" onDragStart={(event) => {
            event.stopPropagation()
            event.dataTransfer.effectAllowed = 'move'
            event.dataTransfer.setData(SUBJECT_DRAG_MIME, JSON.stringify(slot))
          }} onDragOver={(event) => {
            if (!event.dataTransfer.types.includes(SUBJECT_DRAG_MIME)) return
            event.preventDefault()
            event.stopPropagation()
            event.dataTransfer.dropEffect = disabled ? 'none' : 'move'
          }} onDrop={(event) => {
            event.preventDefault()
            event.stopPropagation()
            const source = readSubjectDragSlot(event.dataTransfer)
            if (source) reorderSubjectFiles(source, slot)
          }}>
            <span className="subject-reference-preview__image" role="img" aria-label={`${label}预览：${reference.file.name}`}><LocalFileThumbnail file={reference.file} alt="" /></span>
            <span>{label}</span>
            {uploadStates ? <UploadStatus state={uploadStates.get(reference.file) ?? { phase: 'preview' }} /> : null}
            <button type="button" className="material-remove" aria-label={`移除${label}“${reference.file.name}”`} disabled={disabled} onClick={() => {
              const remaining = references.filter((_, fileIndex) => fileIndex !== index).map((item) => item.file)
              onUploadedFilesChange(fillSubjectDropSlots(createEmptySubjectReferenceFiles(), remaining).files)
              setSelectionError('')
              if (!remaining.length) onModeChange('none')
            }}><X aria-hidden="true" size={15} /></button>
          </div>
        })}
        {className.includes("v6-subject") && references.length < 2 + SUBJECT_MAX_DETAIL_IMAGES ? uploadButton : null}
      </div> : null}
    </div> : null}

    {mode === 'history' ? <div className="subject-input-panel__history">
      {elementsLoading ? <div className="subject-input-panel__history-state" role="status">
        <LoaderCircle className="spin" aria-hidden="true" size={17} />
        <span>正在加载历史主体…</span>
      </div> : null}

      {!elementsLoading && usableElements.length ? <>
        <div className="subject-history-grid" role="group" aria-label="可用历史主体">
          {visibleElements.map((element) => {
            const isSelected = element.id === selectedElementId
            return <button
              key={element.id}
              type="button"
              className={`subject-history-card${isSelected ? ' is-selected' : ''}`}
              disabled={disabled}
              aria-pressed={isSelected}
              onClick={() => selectHistoryElement(element.id)}
            >
              <span className="subject-history-card__media">
                {element.imageUrls[0]
                  ? <img src={element.imageUrls[0]} alt="" loading="lazy" />
                  : <PackageOpen aria-hidden="true" size={22} />}
              </span>
              <strong title={element.name || element.id}>{element.name || element.id}</strong>
            </button>
          })}
        </div>
        {usableElements.length > COLLAPSED_HISTORY_COUNT ? <button
          type="button"
          className="subject-history-more"
          disabled={disabled}
          aria-expanded={showAllHistory}
          onClick={() => setShowAllHistory((current) => !current)}
        >
          {showAllHistory ? '收起' : `查看更多（${usableElements.length - COLLAPSED_HISTORY_COUNT}）`}
        </button> : null}
      </> : null}

      {!elementsLoading && !elementsError && !usableElements.length ? <div className="subject-input-panel__history-state">
        <PackageOpen aria-hidden="true" size={18} />
        <span>暂无可用历史主体</span>
      </div> : null}

      {hasMoreElements && onLoadMoreElements ? <button
        type="button"
        className="subject-history-more"
        disabled={disabled || elementsLoading || loadingMoreElements}
        onClick={onLoadMoreElements}
      >{loadingMoreElements ? '加载中…' : '加载更早的历史主体'}</button> : null}

      {elementsError ? <div className="subject-input-panel__local-error" role="alert">
        <AlertTriangle aria-hidden="true" size={16} />
        <span>{elementsError}</span>
        {onReloadElements ? <button type="button" className="text-button" onClick={onReloadElements}>重新加载</button> : null}
      </div> : null}
    </div> : null}

    {panelErrors.length ? <div id={errorId} className="subject-input-panel__errors" role="alert">
      <AlertTriangle aria-hidden="true" size={16} />
      <div>{panelErrors.map((message) => <p key={message}>{message}</p>)}</div>
    </div> : null}
  </section>
}
