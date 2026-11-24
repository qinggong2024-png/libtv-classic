import { useState } from 'react'
import { CheckCircle2, Clock3, AlertCircle } from 'lucide-react'
import type { AdminVideoTask, AdminVideoTrace } from '../../types'

export type ReviewTimelineTone = 'ok' | 'warning' | 'critical' | 'missing'
export type ReviewTimelineStep = {
  id: string
  label: string
  at: string | null
  elapsedSeconds: number | null
  tone: ReviewTimelineTone
}
const validTime = (value: string | null | undefined): number | null => {
  if (!value) return null
  const ms = Date.parse(value)
  return Number.isFinite(ms) ? ms : null
}
const timeSpan = (start: string | null, end: string | null): number | null => {
  const a = validTime(start)
  const b = validTime(end)
  return a !== null && b !== null && b >= a ? Math.round((b - a) / 1000) : null
}
const latest = (values: Array<string | null | undefined>): string | null => {
  const ordered = values.filter((value): value is string => validTime(value) !== null)
    .sort((a, b) => Date.parse(b) - Date.parse(a))
  return ordered[0] || null
}
const earliest = (values: Array<string | null | undefined>): string | null => {
  const ordered = values.filter((value): value is string => validTime(value) !== null)
    .sort((a, b) => Date.parse(a) - Date.parse(b))
  return ordered[0] || null
}
const eventTime = (traces: AdminVideoTrace[], source: string, useEarliest = false) => {
  const values = traces.flatMap(trace => trace.timeline
    .filter(event => event.source === source).map(event => event.at))
  return useEarliest ? earliest(values) : latest(values)
}
const knownRisk = (task: AdminVideoTask, code: string): boolean =>
  task.clips.some(clip => clip.health?.reasons.some(issue => issue.code === code))
const failedCut = (task: AdminVideoTask): boolean =>
  knownRisk(task, 'CUT_FAILED') || ['FAILED', 'ERROR', 'REJECTED'].includes((task.cutStatus || '').toUpperCase())

/** Task-level, evidence-based milestone line. Do not treat CUT updatedAt as the edit-completion time. */
function createReviewTimeline(task: AdminVideoTask, traces: AdminVideoTrace[]): ReviewTimelineStep[] {
  const cutHandoffs = traces.flatMap(trace => trace.timeline.filter(event =>
    event.source === 'multi_cutflow_task.created_at' || event.source === 'cutflow_handoff.created_at',
  ).map(event => event.at))
  const stages: Array<{ id: string; label: string; at: string | null; risk?: string; failed?: boolean }> = [
    { id: 'created', label: '任务创建', at: task.createdAt },
    { id: 'analysis', label: '最近分析', at: eventTime(traces, 'parent_task.analysis_started_at', true) },
    { id: 'confirmed', label: '方案确认', at: eventTime(traces, 'generation_variant.confirmed_at') },
    { id: 'submitted', label: 'Kling 提交', at: eventTime(traces, 'generation_attempt.submitted_at'), risk: 'SLOW_BEFORE_SUBMIT' },
    { id: 'provider', label: 'Kling 完成', at: eventTime(traces, 'generation_attempt.completed_at'), risk: 'SLOW_PROVIDER' },
    { id: 'raw', label: '原片保存', at: eventTime(traces, 'download_artifact.created_at'), risk: 'SLOW_SAVED_RECORD' },
    { id: 'cut', label: failedCut(task) ? 'CUT 异常' : 'CUT 送剪记录', at: earliest(cutHandoffs), failed: failedCut(task), risk: 'CUT_UNCERTAIN' },
    { id: 'final', label: '成片保存', at: task.cut?.createdAt ?? null },
  ]
  return stages.map((step, index) => ({
    id: step.id,
    label: step.label,
    at: step.at && validTime(step.at) !== null ? step.at : null,
    // Intervals are between adjacent saved milestones, not cumulative stage totals.
    elapsedSeconds: index ? timeSpan(stages[index - 1].at, step.at) : null,
    tone: step.failed ? 'critical'
      : !step.at || validTime(step.at) === null ? 'missing'
      : (step.risk && knownRisk(task, step.risk)) || (step.id === 'analysis' && (timeSpan(task.createdAt, step.at) ?? 0) >= 3600) ? 'warning' : 'ok',
  }))
}

const clock = (value: string | null) => {
  if (!value) return '未记录'
  return new Date(value).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
const span = (seconds: number | null) => {
  if (seconds === null) return '—'
  return seconds >= 3600 ? `${Math.floor(seconds / 3600)}时${Math.floor((seconds % 3600) / 60)}分`
    : seconds >= 60 ? `${Math.floor(seconds / 60)}分${seconds % 60}秒`
      : `${seconds}秒`
}

export function AdminReviewTimeline({ task, traces, loading }: {
  task: AdminVideoTask
  traces: AdminVideoTrace[]
  loading: boolean
}) {
  const steps = createReviewTimeline(task, traces)
  const end = task.cut?.createdAt || steps.find(step => step.id === 'raw')?.at || null
  const elapsed = timeSpan(task.createdAt, end)
  const severe = failedCut(task)
  const [selected, setSelected] = useState<string | null>(null)
  const active = steps.find(step => step.id === selected) ?? steps.find(step => step.tone === 'critical' || step.tone === 'warning') ?? steps.find(step => step.id === (task.cut ? 'final' : 'raw')) ?? steps[0]
  const analysis = steps.find(step => step.id === 'analysis')
  const analysisSpan = timeSpan(analysis?.at ?? null, end)
  const preAnalysis = timeSpan(task.createdAt, analysis?.at ?? null)
  const attention = steps.some(step => step.tone === 'warning' || step.tone === 'critical')
  return <section className="admin-review-timeline" aria-label="生产时间节点">
    <header className="admin-review-timeline-header">
      <strong>{task.cut ? <CheckCircle2 size={18} /> : severe ? <AlertCircle size={18} /> : <Clock3 size={18} />}{task.cut ? '成片已保存' : severe ? 'CUT 存在异常' : '生产时间线'}</strong>
      <span data-attention={attention}>{loading ? '读取中' : attention ? '存在待核实环节' : task.cut ? clock(task.cut.createdAt) : '生产记录'}</span>
    </header>
    <div className="admin-review-metrics">
      <div><small>{task.cut ? '创建至成片' : '创建至原片'}</small><strong>{loading ? '—' : span(elapsed)}</strong></div>
      <div><small>{task.cut ? '最近分析至成片' : '最近分析至原片'}</small><strong>{loading ? '—' : span(analysisSpan)}</strong></div>
      <div data-tone={analysis?.tone}><small>分析前跨度</small><strong>{loading ? '—' : span(preAnalysis)}</strong></div>
    </div>
    <ol className="admin-review-timeline-steps">
      {steps.map((step, index) => <li key={step.id} className="admin-review-step"
        data-tone={loading ? 'missing' : step.tone}
        title={step.at ? new Date(step.at).toLocaleString('zh-CN') : '无可核实的时间记录'}>
        <button type="button" className="admin-review-segment" aria-pressed={active.id === step.id} onClick={() => setSelected(step.id)} disabled={loading}>
          <span className="admin-review-segment-bar" aria-hidden="true" />
          <strong>{step.label}</strong>
          <small>{loading || index === 0 ? '—' : span(step.elapsedSeconds)}</small>
        </button>
      </li>)}
    </ol>
    <div className="admin-review-selected" data-tone={active.tone} aria-live="polite">
      <strong>{active.label}</strong><time dateTime={active.at || undefined}>{loading ? '—' : clock(active.at)}</time><span>{loading ? '—' : span(active.elapsedSeconds)}</span>
      <span className="admin-review-selected-state">{loading ? '' : active.tone === 'critical' ? '异常' : active.tone === 'warning' ? '待核实' : active.tone === 'missing' ? '未记录' : '已记录'}</span>
    </div>
  </section>
}
