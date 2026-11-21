import { errorMessage } from '../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { Download, Eye, Film, LoaderCircle, Play, RotateCcw, Scissors, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useRef, useState } from 'react'
import { saveArtifact, setVariantFeedback } from '../../api'
import { type FeedbackValue, type Task, type Variant } from '../../types'
import { GenerationFeedbackPanel } from '../../GenerationFeedback'
import { StatusBadge } from '../../shared/components/Badges'
import { AuthenticatedImage } from '../../shared/components/Media'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'
import { formatTime, variantDisplayName, variantFileToken, variantLabelClass, variantShortLabel } from '../../domain/taskPresentation'
import { CutflowSourceToggle } from './CutflowSourceToggle'

export function ResultCard({ task, variant, onPlay, onRemix, onOpen, onEditing, onFeedbackChanged, canGenerate = true, basketSelected, onToggleBasket, basketDisabled, basketDisabledLabel, basketActionLabel }: { task: Task; variant: Variant; onPlay: () => void; onRemix: () => void; onOpen: () => void; onEditing?: () => void; onFeedbackChanged: (task: Task) => void; canReview?: boolean; canGenerate?: boolean; basketSelected?: boolean; onToggleBasket?: () => void; basketDisabled?: boolean; basketDisabledLabel?: string; basketActionLabel?: string }) {
  const product = [...task.assets].sort((a, b) => a.order - b.order)[0]
  const ready = Boolean(variant.downloadArtifactId)
  const duration = task.settings?.deepVideoMode === 'depth' ? task.settings?.sourceDurationSeconds : task.settings?.durationSeconds
  const [feedbackBusy, setFeedbackBusy] = useState<FeedbackValue | null>(null)
  const [feedbackMessage, setFeedbackMessage] = useState('')
  const [animatedFeedback, setAnimatedFeedback] = useState<FeedbackValue | null>(null)
  const [feedbackPanel, setFeedbackPanel] = useState<FeedbackValue | null>(null)
  const [downloadBusy, setDownloadBusy] = useState(false)
  const [downloadError, setDownloadError] = useState('')
  const feedbackAnchorRef = useRef<HTMLDivElement>(null)
  const submitFeedback = async (value: FeedbackValue) => {
    const nextValue = variant.feedback === value ? null : value
    setFeedbackBusy(value)
    setFeedbackMessage('')
    try {
      const changed = await setVariantFeedback(task.id, variant.id, nextValue)
      onFeedbackChanged(changed)
      setAnimatedFeedback(nextValue)
      setFeedbackPanel(nextValue)
      setFeedbackMessage(nextValue ? '已记录，可选择具体原因' : '已取消反馈')
      window.setTimeout(() => setAnimatedFeedback(null), 320)
    } catch (cause) {
      setFeedbackMessage(errorMessage(cause, '反馈提交失败，请重试'))
    } finally {
      setFeedbackBusy(null)
    }
  }
  const saveFeedbackDetails = async (reasons: Variant['feedbackReasons'], comment: string | null) => {
    if (!feedbackPanel) return false
    setFeedbackBusy(feedbackPanel)
    setFeedbackMessage('')
    try {
      const changed = await setVariantFeedback(task.id, variant.id, feedbackPanel, { reasons, comment })
      onFeedbackChanged(changed)
      setFeedbackMessage('详细反馈已保存，将优化后续同类场景')
      return true
    } catch (cause) {
      setFeedbackMessage(errorMessage(cause, '详细反馈保存失败，请重试'))
      return false
    } finally {
      setFeedbackBusy(null)
    }
  }
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
  return <article className={`result-card${feedbackPanel ? ' has-feedback-panel' : ''}${basketSelected ? ' is-basket-selected' : ''}`} onClick={(event) => {
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
    {ready ? <div ref={feedbackAnchorRef} className="result-feedback">
      <span className="feedback-copy" aria-live="polite">{feedbackMessage || (variant.feedback ? '感谢反馈，系统会持续学习' : '这个结果怎么样？')}</span>
      <span className="feedback-buttons">
        <button className={`feedback-button${variant.feedback === 'LIKE' ? ' is-selected is-positive' : ''}${animatedFeedback === 'LIKE' ? ' is-animating' : ''}`} disabled={feedbackBusy !== null} onClick={() => void submitFeedback('LIKE')} aria-label={variant.feedback === 'LIKE' ? '取消点赞' : '点赞此视频'} aria-pressed={variant.feedback === 'LIKE'} title="点赞"><span>{feedbackBusy === 'LIKE' ? <LoaderCircle className="spin" size={17} /> : <ThumbsUp size={18} />}</span></button>
        <button className={`feedback-button${variant.feedback === 'DISLIKE' ? ' is-selected is-negative' : ''}${animatedFeedback === 'DISLIKE' ? ' is-animating' : ''}`} disabled={feedbackBusy !== null} onClick={() => void submitFeedback('DISLIKE')} aria-label={variant.feedback === 'DISLIKE' ? '取消点踩' : '点踩此视频'} aria-pressed={variant.feedback === 'DISLIKE'} title="点踩"><span>{feedbackBusy === 'DISLIKE' ? <LoaderCircle className="spin" size={17} /> : <ThumbsDown size={18} />}</span></button>
      </span>
      {feedbackPanel ? <GenerationFeedbackPanel
        key={`${variant.id}-${feedbackPanel}`}
        value={feedbackPanel}
        initialReasons={variant.feedback === feedbackPanel ? (variant.feedbackReasons ?? []) : []}
        initialComment={variant.feedback === feedbackPanel ? variant.feedbackComment : null}
        anchorRef={feedbackAnchorRef}
        onClose={() => setFeedbackPanel(null)}
        onSave={saveFeedbackDetails}
      /> : null}
    </div> : null}
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
