import { Check, CircleAlert, Film, Play } from 'lucide-react'
import { useRef, useState } from 'react'
import { TaskFinalDelivery } from './TaskFinalDelivery'
import { RemoteVideoPreview } from '../../shared/components/Media'
import { markMultiCutflowOutputViewed, type MultiCutflowTask } from '../../api'
import { formatTime } from '../../domain/taskPresentation'
import { multiCutflowPresentation } from '../../domain/cutflowPresentation'

export function CutflowBatchResult({ task, sourceLabel, onViewed, latest = false }: {
  task: MultiCutflowTask
  sourceLabel?: (source: MultiCutflowTask['sources'][number]) => string
  onViewed?: () => void
  latest?: boolean
}) {
  const [previewOpen, setPreviewOpen] = useState(false)
  const primaryOutput = task.outputs[0]
  const [acknowledgedKey, setAcknowledgedKey] = useState<string | null>(null)
  const recording = useRef<string | null>(null)
  const viewKey = primaryOutput ? `${task.id}:${primaryOutput.id}` : null
  const viewed = Boolean(primaryOutput?.viewedAt || (viewKey && acknowledgedKey === viewKey))
  const onPlaying = () => {
    if (!primaryOutput || !viewKey || viewed || recording.current === viewKey) return
    recording.current = viewKey
    void markMultiCutflowOutputViewed(task.id, primaryOutput.id)
      .then(() => { setAcknowledgedKey(viewKey); onViewed?.() })
      .catch(() => { recording.current = null })
  }
  const finished = Boolean(primaryOutput)
  const presentation = multiCutflowPresentation(task)
  if (latest && primaryOutput) return <article className="cutflow-batch-result is-finished">
    <TaskFinalDelivery task={task} sourceLabel={sourceLabel} onPreview={() => setPreviewOpen(true)} unseen={primaryOutput.viewedAt === null && !viewed} />
    {previewOpen ? <RemoteVideoPreview mediaUrl={primaryOutput.mediaUrl} title={`剪辑成片 · ${task.sources.length} 条原片`} onClose={() => setPreviewOpen(false)} onPlaying={onPlaying} /> : null}
  </article>
  const sourceTrace = <details className="cutflow-batch-result__source-details">
    <summary>剪辑来源（{task.sources.length} 条）</summary>
    <ol className="cutflow-batch-result__sources" aria-label="剪辑输入顺序">
      {task.sources.map(source => <li key={source.clipId}><b>{source.order}</b><span>{sourceLabel?.(source) || source.title}</span></li>)}
    </ol>
  </details>
  return <article className={`cutflow-batch-result${finished ? ' is-finished' : ''}`}>
    <header>
      <div>
        <span className="cutflow-batch-result__icon" aria-hidden="true">{finished ? <Check size={15} /> : <Film size={15} />}</span>
        <span><strong>{latest && finished ? '最新成片' : finished ? '剪辑成片' : '剪辑任务'}</strong><small>{formatTime(task.updatedAt)}</small></span>
      </div>
      <span className={`cutflow-library__status${finished ? ' is-finished' : ''}`} role="status">{finished ? '成片已回传' : presentation.label}</span>
    </header>
    {!latest || !primaryOutput ? sourceTrace : null}
    {task.errorMessage ? <p role="alert"><CircleAlert size={14} />{task.errorMessage}</p> : null}
    {primaryOutput ? <div className="cutflow-batch-result__output">
      <div className="cutflow-batch-result__media">
        <button type="button" className="cutflow-batch-result__preview" aria-label="预览剪辑成片" onClick={() => setPreviewOpen(true)}>
          <video preload="metadata" muted playsInline src={primaryOutput.mediaUrl} />
          <span><Play size={18} />预览</span>
        </button>
        <a className="cutflow-batch-result__download" href={`${primaryOutput.mediaUrl}?download=true`} download>下载成片</a>
      </div>
      {latest ? <aside className="cutflow-batch-result__insights" aria-label="成片信息与下一步操作">
        <div className="cutflow-batch-result__meta"><strong>剪辑成片 {primaryOutput.viewedAt === null && !viewed ? '· 未看' : ''}</strong><small>{primaryOutput.durationSeconds.toFixed(1)} 秒 · {primaryOutput.width}×{primaryOutput.height}</small></div>
        {sourceTrace}
        <nav className="cutflow-batch-result__shortcuts" aria-label="相关操作位置">
          <button type="button" onClick={() => {
            const element = document.getElementById('task-original-clips')
            if (element instanceof HTMLDetailsElement) element.open = true
            element?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }}>查看原片</button>
          <button type="button" onClick={() => document.getElementById('task-cutflow-records')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>查看剪辑记录</button>
        </nav>
      </aside> : <div className="cutflow-batch-result__meta"><strong>剪辑成片 {primaryOutput.viewedAt === null && !viewed ? '· 未看' : ''}</strong><small>{primaryOutput.durationSeconds.toFixed(1)} 秒 · {primaryOutput.width}×{primaryOutput.height}</small></div>}
    </div> : null}
    {previewOpen && primaryOutput ? <RemoteVideoPreview mediaUrl={primaryOutput.mediaUrl} title={`剪辑成片 · ${task.sources.length} 条原片`} onClose={() => setPreviewOpen(false)} onPlaying={onPlaying} /> : null}
  </article>
}
