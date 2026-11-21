import { ChevronRight, Clock3, Layers3 } from 'lucide-react'
import { useState } from 'react'
import type { MultiCutflowTask } from '../../api'
import { formatTime } from '../../domain/taskPresentation'
import { multiCutflowPresentation } from '../../domain/cutflowPresentation'
import { CutflowBatchResult } from './CutflowBatchResult'

const newestFirst = (a: MultiCutflowTask, b: MultiCutflowTask) =>
  Date.parse(b.updatedAt) - Date.parse(a.updatedAt)

const batchFingerprint = (batch: MultiCutflowTask) => JSON.stringify({
  updatedAt: batch.updatedAt,
  status: batch.status,
  errorMessage: batch.errorMessage,
  sources: batch.sources.map(source => [source.clipId, source.order, source.variantId, source.artifactId]),
  outputs: batch.outputs.map(output => output.id),
})

const uniqueBatches = (batches: MultiCutflowTask[]) => {
  const seen = new Set<string>()
  return batches.filter(batch => {
    const fingerprint = batchFingerprint(batch)
    if (seen.has(fingerprint)) return false
    seen.add(fingerprint)
    return true
  })
}

function BatchSummary({ batch, archived = false, flat = false }: { batch: MultiCutflowTask; archived?: boolean; flat?: boolean }) {
  const state = multiCutflowPresentation(batch)
  if (flat && batch.status === 'FAILED') return <article className="task-cut-entry task-cut-entry--flat is-archived">
    <header className="task-cut-entry__failure-heading"><span className="task-cut-entry__main"><strong>{batch.sources.length > 1 ? '合并剪辑' : '单片剪辑'} · {batch.sources.length} 条原片</strong><small>{formatTime(batch.updatedAt)}</small></span><span className="task-cut-entry__status is-failed">{state.label}</span></header>
    {batch.errorMessage ? <p role="alert" className="task-cut-entry__error">{batch.errorMessage}</p> : null}
    <div className="task-cut-entry__sources">{batch.sources.map(source => <span key={source.clipId}><b>{source.order}.</b> {source.title}</span>)}</div>
    <small>剪辑批次：{batch.id}</small>
  </article>
  if (flat) return <details className={`task-cut-entry task-cut-entry--compact${archived ? ' is-archived' : ''}`}>
    <summary>
      <span className="task-cut-entry__main">
        <strong>{batch.outputs.length ? '剪辑完成' : batch.sources.length > 1 ? '合并剪辑' : '单片剪辑'} <span>· {batch.sources.length} 条原片</span></strong>
        <small>{formatTime(batch.updatedAt)}</small>
      </span>
      <span className={`task-cut-entry__status${state.active ? ' is-active' : batch.outputs.length ? ' is-finished' : batch.status === 'FAILED' ? ' is-failed' : ''}`}>{state.label}</span>
      <ChevronRight size={16} className="task-cut-entry__arrow" />
    </summary>
    <div className="task-cut-entry__details">
      <div className="task-cut-entry__sources">{batch.sources.map(source => <span key={source.clipId}><b>{source.order}.</b> {source.title}</span>)}</div>
      {batch.errorMessage ? <p role="alert" className="task-cut-entry__error">{batch.errorMessage}</p> : null}
      <small>剪辑批次：{batch.id}</small>
    </div>
  </details>
  return <details className={`task-cut-entry${archived ? ' is-archived' : ''}`}>
    <summary>
      <span className="task-cut-entry__mark"><Layers3 size={16} /></span>
      <span className="task-cut-entry__main">
        <strong>{batch.sources.length > 1 ? '多片合并剪辑' : '单片剪辑'} <span>· {batch.sources.length} 条原片</span></strong>
        <small>{formatTime(batch.updatedAt)} · {batch.outputs.length ? `已回传 ${batch.outputs.length} 条成片` : '原片已送剪'}</small>
      </span>
      <span className={`task-cut-entry__status${state.active ? ' is-active' : batch.outputs.length ? ' is-finished' : batch.status === 'FAILED' ? ' is-failed' : ''}`}>{state.label}</span>
      <ChevronRight size={16} className="task-cut-entry__arrow" />
    </summary>
    <div className="task-cut-entry__details">
      <div className="task-cut-entry__sources">{batch.sources.map(source =>
        <span key={source.clipId}><b>{source.order}.</b> {source.title}</span>)}</div>
      {batch.errorMessage ? <p role="alert" className="task-cut-entry__error">{batch.errorMessage}</p> : null}
      <small>剪辑批次：{batch.id}</small>
    </div>
  </details>
}

export function TaskCutflowActivity({ batches, inlineHistory = false }: { batches: MultiCutflowTask[]; inlineHistory?: boolean }) {
  const [showAll, setShowAll] = useState(false)
  const deduped = uniqueBatches(batches)
  const active = deduped.filter(batch => multiCutflowPresentation(batch).active).sort(newestFirst)
  const historical = deduped.filter(batch => !multiCutflowPresentation(batch).active).sort(newestFirst)
  const failed = historical.filter(batch => batch.status === 'FAILED').length
  const canceled = historical.filter(batch => batch.status === 'CANCELED').length
  const displayHistorical = showAll ? historical : historical.slice(0, 5)
  return <>
    {active.length ? <div className="task-cut-activity">
      <div className="task-cut-activity__label"><Clock3 size={15} /> 当前进行中 <strong>{active.length}</strong></div>
      {active.map(batch => <BatchSummary batch={batch} flat={inlineHistory} key={batch.id} />)}
    </div> : !inlineHistory ? <p className="task-cut-activity__empty">当前没有进行中的剪辑任务。</p> : null}
    {historical.length ? inlineHistory ? <div className="task-cut-history__content">{displayHistorical.map(batch => <BatchSummary batch={batch} archived flat key={batch.id} />)}{historical.length > 5 ? <button className="text-button" type="button" onClick={() => setShowAll(value => !value)}>{showAll ? '收起较早记录' : `查看全部 ${historical.length} 条剪辑记录`}</button> : null}</div> : <details className="task-cut-history">
      <summary><span>历史剪辑批次 <strong>{historical.length}</strong><small>{failed ? `失败 ${failed} · ` : ''}{canceled ? `取消 ${canceled} · ` : ''}其余已结束</small></span><ChevronRight size={16} /></summary>
      <div className="task-cut-history__content">
        {displayHistorical.map(batch => <BatchSummary batch={batch} archived key={batch.id} />)}
        {historical.length > 5 ? <button className="text-button" type="button" onClick={() => setShowAll(current => !current)}>{showAll ? '收起较早记录' : `查看全部 ${historical.length} 个历史批次`}</button> : null}
      </div>
    </details> : null}
  </>
}

export function TaskCutflowDeliveries({ batches, sourceLabel, onViewed, display = 'all' }: {
  batches: MultiCutflowTask[]
  sourceLabel: (source: MultiCutflowTask['sources'][number]) => string
  onViewed?: () => void
  display?: 'all' | 'latest' | 'history'
}) {
  const finished = uniqueBatches(batches).filter(batch => batch.outputs.length > 0).sort(newestFirst)
  const latest = finished[0]
  return <>
    {latest && display !== 'history' ? <div className="task-cut-delivery-preview">
      <CutflowBatchResult task={latest} sourceLabel={sourceLabel} latest onViewed={onViewed} />
    </div> : null}
    {finished.length > 1 && display === 'history' ? <div className="task-cut-delivery-older">
      {finished.slice(1).map(batch => <CutflowBatchResult key={batch.id} task={batch} sourceLabel={sourceLabel} onViewed={onViewed} />)}
    </div> : null}
    {finished.length > 1 && display === 'all' ? <details className="task-cut-history task-cut-delivery-history">
      <summary><span>较早成片版本 <strong>{finished.length - 1}</strong></span><ChevronRight size={16} /></summary>
      <div className="task-cut-delivery-older">{finished.slice(1).map(batch => <CutflowBatchResult key={batch.id} task={batch} sourceLabel={sourceLabel} onViewed={onViewed} />)}</div>
    </details> : null}
  </>
}
