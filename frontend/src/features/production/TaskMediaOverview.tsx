import { Check, CircleAlert, Download, LoaderCircle, Play } from 'lucide-react'
import { useRef, useState } from 'react'
import { markMultiCutflowOutputViewed, saveArtifact, saveAsset, type MultiCutflowTask } from '../../api'
import type { Task, Variant } from '../../types'
import { taskStatus } from '../../domain/taskPresentation'
import type { CutflowDispatchPresentation } from './cutflowDispatchPresentation'
import { ACTIVE_PHASES } from '../../domain/workflowStates'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'
import { RemoteVideoPreview } from '../../shared/components/Media'
import { taskPlayableMedia, type PlayableMedia } from '../task-center/taskCenterMedia'

function FlowStages({ plan, originalsReady, originalsTotal, originalsFailed, generationActive, cutflowActive, cutflowAttention, hasFinal }: {
  plan: boolean; originalsReady: number; originalsTotal: number; originalsFailed: boolean; generationActive: boolean; cutflowActive: boolean; cutflowAttention: boolean; hasFinal: boolean
}) {
  const steps = [
    { label: '方案', state: plan ? 'done' : 'pending' },
    { label: '原片', state: originalsReady === originalsTotal && originalsTotal > 0 ? 'done' : generationActive ? 'active' : originalsFailed ? 'attention' : originalsReady > 0 ? 'partial' : 'pending' },
    { label: '剪辑', state: hasFinal ? 'done' : cutflowAttention ? 'attention' : cutflowActive ? 'active' : 'pending' },
    { label: '交付', state: hasFinal ? 'done' : 'pending' },
  ]
  return <div className="allwe-detail-stages" aria-label="生产流程">{steps.map((step, i) =>
    <div key={step.label} className={'allwe-detail-stages__step is-' + step.state} aria-current={step.state === 'active' ? 'step' : undefined}>
      <span aria-hidden="true">{step.state === 'done' ? <Check size={15} /> : step.state === 'active' ? <span className="allwe-detail-stages__dot" /> : step.state === 'attention' || step.state === 'partial' ? <CircleAlert size={14} /> : i + 1}</span>
      <small>{step.label}</small>
    </div>)}</div>
}

export function TaskMediaOverview({ task, batches, onPlayOriginal, cutflowActive, cutflowStatus, canOperateCutflow, onCutflowAction, issue, onViewed }: {
  task: Task
  batches: MultiCutflowTask[]
  onPlayOriginal: (variant: Variant) => void
  cutflowActive: boolean
  cutflowStatus: CutflowDispatchPresentation
  canOperateCutflow: boolean
  onCutflowAction: () => void
  issue?: string
  onViewed?: () => void
}) {
  const [showFinal, setShowFinal] = useState(false)
  const recordedViews = useRef(new Set<string>())
  const [downloading, setDownloading] = useState('')
  const [downloadError, setDownloadError] = useState('')
  const allMedia = taskPlayableMedia(task, batches)
  const final = allMedia.find(media => media.kind === 'final')
  const originalMedia = allMedia.filter(media => media.kind === 'original')
  const primary = final ?? originalMedia[0]
  const onFinalPlaying = () => {
    if (final?.kind !== 'final' || final.viewedAt || recordedViews.current.has(final.key)) return
    const batch = batches.find(item => item.id === final.batchId)
    if (!batch?.sources.length) return // Legacy single-source handoffs use their own review flow.
    recordedViews.current.add(final.key)
    void markMultiCutflowOutputViewed(final.batchId, final.outputId)
      .then(() => onViewed?.())
      .catch(() => recordedViews.current.delete(final.key))
  }
  const total = task.variants.length
  const ready = originalMedia.length
  const originalsFailed = task.variants.some(variant => variant.generationStatus === 'FAILED' || variant.downloadStatus === 'FAILED' || variant.validationStatus === 'FAILED')
  const sortedBatches = [...batches].sort((a,b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))
  const recentBatch = sortedBatches[0]
  const hasUnfinishedCut = Boolean(recentBatch) || cutflowActive || cutflowStatus.action === 'refresh' || cutflowStatus.action === 'history' || cutflowStatus.waiting
  const statusLabel = final ? '成片已回传'
    : hasUnfinishedCut && ready > 0 ? cutflowStatus.headline
    : ready === total && total > 0 && cutflowStatus.action === 'select' ? '原片已就绪 · 待送剪'
    : originalsFailed && ready > 0 ? '部分原片可用'
    : ready > 0 ? '原片已就绪'
    : taskStatus(task).label
  const save = async (media: PlayableMedia) => {
    if (downloading) return
    setDownloadError('')
    setDownloading(media.key)
    try {
      if (media.kind === 'final') {
        await saveAsset(media.mediaUrl + (media.mediaUrl.includes('?') ? '&' : '?') + 'download=true', 'ALLWE-成片.mp4')
      } else {
        await saveArtifact(media.artifactId, 'ALLWE-' + media.label.replace(/\s+/g, '-') + '.mp4')
      }
    } catch {
      setDownloadError('下载失败，请重试')
    } finally {
      setDownloading('')
    }
  }
  const preview = (media: PlayableMedia) => {
    if (media.kind === 'final') setShowFinal(true)
    else onPlayOriginal(media.variant)
  }
  return <>
    <FlowStages plan={Boolean(task.compiledPlan || task.analysisResult)} originalsReady={ready} originalsTotal={total} originalsFailed={originalsFailed} generationActive={ACTIVE_PHASES.has(task.phase)}
      cutflowAttention={!final && cutflowStatus.tone === 'attention' && cutflowStatus.action !== 'select'} cutflowActive={cutflowActive || Boolean(ready > 0 && cutflowStatus.waiting)} hasFinal={Boolean(final)} />
    <div className="allwe-detail-overview">
      <section className="allwe-detail-overview__media" aria-label="视频预览">
        <div className="allwe-detail-overview__primary">
          <header><strong>{final ? '最终成片' : ready ? '已生成原片' : '任务进度'}</strong></header>
          {primary ? <button className="allwe-detail-overview__poster" type="button" onClick={() => preview(primary)} aria-label={'播放' + primary.label}>
            {primary.kind === 'final'
              ? <video muted playsInline preload="metadata" src={primary.mediaUrl} />
              : <VideoThumbnail artifactId={primary.artifactId} alt={primary.label} />}
            <span className="allwe-detail-overview__play"><Play size={22} fill="currentColor" /></span>
          </button> : <div className="allwe-detail-overview__poster allwe-detail-overview__placeholder" role="status">
            <LoaderCircle size={23} aria-hidden="true" /><span>{taskStatus(task).label}</span>
          </div>}
          {primary ? <button className="allwe-detail-overview__download" type="button" disabled={Boolean(downloading)} onClick={() => void save(primary)}>
            {downloading === primary.key ? <LoaderCircle size={13} className="spin" /> : <Download size={13} />}
            {primary.kind === 'final' ? '下载成片' : '下载原片'}
          </button> : null}
        </div>
        <div className="allwe-detail-overview__originals">
          <header><strong>生成原片</strong><small>{ready} / {total}</small></header>
          <div className="allwe-detail-overview__clips">
            {task.variants.map((variant, index) => {
              const media = originalMedia.find(item => item.kind === 'original' && item.variant.id === variant.id)
              return <div className="allwe-detail-overview__clip" key={variant.id}>
                {media?.kind === 'original' ? <button className="allwe-detail-overview__thumb" type="button" onClick={() => onPlayOriginal(variant)} aria-label={'播放原片 ' + (index + 1)}>
                  <VideoThumbnail artifactId={media.artifactId} alt={'原片 ' + (index + 1)} />
                  <span className="allwe-detail-overview__play"><Play size={15} fill="currentColor" /></span>
                </button> : <div className="allwe-detail-overview__thumb allwe-detail-overview__thumb--pending" role="status">{variant.generationStatus === 'FAILED' || variant.downloadStatus === 'FAILED' || variant.validationStatus === 'FAILED' ? <CircleAlert size={17} aria-hidden="true" /> : <LoaderCircle size={17} aria-hidden="true" />}</div>}
                <div className="allwe-detail-overview__clip-meta"><span>原片 {index + 1}</span>
                  {media ? <button type="button" disabled={Boolean(downloading)} onClick={() => void save(media)} aria-label={'下载原片 ' + (index + 1)}><Download size={13} /></button> : <small>{variant.generationStatus === 'FAILED' || variant.downloadStatus === 'FAILED' || variant.validationStatus === 'FAILED' ? '失败' : variant.generationStatus === 'UNKNOWN' ? '状态不明' : '未就绪'}</small>}
                </div>
              </div>
            })}
          </div>
        </div>
      </section>
      <aside className="allwe-detail-overview__status">
        <h2>当前状态</h2><strong className="allwe-detail-overview__state">{statusLabel}</strong>
        {!final && ready > 0 && cutflowStatus.action !== 'select' ? <p role={cutflowStatus.tone === 'attention' ? 'alert' : 'status'}>{cutflowStatus.detail}</p> : null}
        {!final && ready > 0 && cutflowStatus.actionLabel && (cutflowStatus.action !== 'select' || canOperateCutflow) ? <button type="button" className="allwe-detail-overview__action" onClick={onCutflowAction}>{cutflowStatus.actionLabel}</button> : null}
        <dl><dt>原片</dt><dd>{ready} / {total}</dd><dt>成片</dt><dd>{final ? '已回传' : '尚未回传'}</dd></dl>
        {issue ? <div className="allwe-detail-overview__issue" role="alert">{issue}</div> : null}
        {downloadError ? <div className="allwe-detail-overview__issue" role="alert">{downloadError}</div> : null}
      </aside>
    </div>
    {showFinal && final?.kind === 'final' ? <RemoteVideoPreview mediaUrl={final.mediaUrl} title="最终成片" onClose={() => setShowFinal(false)} onPlaying={onFinalPlaying} /> : null}
  </>
}
