import { errorMessage } from '../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { Download, Eye, Film, LoaderCircle, Play, RotateCcw, Scissors } from 'lucide-react'
import { useState } from 'react'
import { saveArtifact } from '../../api'
import { type Task, type Variant } from '../../types'
import { VariantFeedback } from './VariantFeedback'
import { StatusBadge } from '../../shared/components/Badges'
import { AuthenticatedImage } from '../../shared/components/Media'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'
import { formatTime, variantDisplayName, variantFileToken, variantLabelClass, variantShortLabel } from '../../domain/taskPresentation'
import { CutflowSourceToggle } from './CutflowSourceToggle'

export function ResultCard({ task, variant, onPlay, onRemix, onOpen, onEditing, onFeedbackChanged, canGenerate = true, basketSelected, onToggleBasket, basketDisabled, basketDisabledLabel, basketActionLabel }: { task: Task; variant: Variant; onPlay: () => void; onRemix: () => void; onOpen: () => void; onEditing?: () => void; onFeedbackChanged: (task: Task) => void; canReview?: boolean; canGenerate?: boolean; basketSelected?: boolean; onToggleBasket?: () => void; basketDisabled?: boolean; basketDisabledLabel?: string; basketActionLabel?: string }) {
  const product = [...task.assets].sort((a, b) => a.order - b.order)[0]
  const ready = Boolean(variant.downloadArtifactId)
  const duration = task.settings?.deepVideoMode === 'depth' ? task.settings?.sourceDurationSeconds : task.settings?.durationSeconds
  const [downloadBusy, setDownloadBusy] = useState(false)
  const [downloadError, setDownloadError] = useState('')
  const download = async () => {
    if (!variant.downloadArtifactId) return
    setDownloadBusy(true)
    setDownloadError('')
    try {
      await saveArtifact(variant.downloadArtifactId, `${task.title}-${variantFileToken(variant)}.mp4`)
    } catch (cause) {
      setDownloadError(errorMessage(cause, '视频下载失败，请稍后重试'))
    } finally {
      setDownloadBusy(false)
    }
  }
  return <article className={`result-card${basketSelected ? ' is-basket-selected' : ''}`} onClick={(event) => {
    const target = event.target
    if (!(target instanceof Element) || !event.currentTarget.contains(target)) return
    if (target.closest('button, a, input, textarea, select, label, video, summary, [role="button"], [data-card-interactive]') || window.getSelection()?.toString()) return
    onOpen()
  }}>
    <button className="result-cover" onClick={ready ? onPlay : onOpen} aria-label={ready ? `播放 ${task.title}` : `查看 ${task.title}`}>
      {variant.downloadArtifactId ? <VideoThumbnail artifactId={variant.downloadArtifactId} alt={`${task.title} 生成原片封面`} /> : product ? <AuthenticatedImage src={product.thumbnailUrl} alt={`${task.title} 视频封面`} /> : <Film size={30} />}
      <span className={`variant-label ${variantLabelClass(variant)}`}>{variantShortLabel(variant)}</span>
      {ready ? <span className="play-button"><Play size={21} fill="currentColor" /></span> : <span className="result-state"><StatusBadge value={variant.generationStatus} retryable={variant.retryable ?? variant.providerErrorRetryable} hasArtifact={ready} /></span>}
      <span className="duration">{typeof duration === 'number' ? `${Number(duration.toFixed(2))}s` : '10s'}</span>
    </button>
    <div className="result-body"><div><strong>{task.title}</strong><span>生成原片 · {variantDisplayName(variant)} · {formatTime(task.updatedAt)}</span></div>
    {ready ? <VariantFeedback task={task} variant={variant} onFeedbackChanged={onFeedbackChanged} /> : null}
    <div className="result-actions">
      {ready ? <><button className="icon-button" onClick={onPlay} aria-label="播放生成原片" title="播放生成原片"><Play size={17} /></button><button className="icon-button" disabled={downloadBusy} onClick={() => void download()} aria-label="下载生成原片" title="下载生成原片">{downloadBusy ? <LoaderCircle className="spin" size={17} /> : <Download size={17} />}</button><button className="icon-button" onClick={onOpen} aria-label="查看任务详情" title="查看任务详情"><Eye size={17} /></button></> : <button className="compact-button" onClick={onOpen}>查看状态</button>}
      {canGenerate ? <button className="remix-button" onClick={onRemix}><RotateCcw size={15} />同素材再制作</button> : null}
    </div>{ready ? (onToggleBasket
      ? <CutflowSourceToggle
        selected={Boolean(basketSelected)}
        disabled={Boolean(basketDisabled)}
        disabledLabel={basketDisabledLabel}
        actionLabel={basketActionLabel}
        onToggle={onToggleBasket}
      />
      : <button type="button" className="text-button result-editing-entry" onClick={onEditing ?? onOpen}><Scissors size={15} />剪辑与成片</button>) : null}{downloadError ? <small className="result-action-error" role="alert">{downloadError}</small> : null}</div>
  </article>
}
