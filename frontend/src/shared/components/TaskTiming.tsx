// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { AlertTriangle, Check, ChevronDown, CircleHelp, LoaderCircle, Pause, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { type Task } from '../../types'
import type { CutflowHandoff, MultiCutflowTask } from '../../api'
import { formatDurationSeconds, formatElapsed, parseTaskTimestamp, visibleTaskTimingStageKeys } from '../../taskTiming'
import { providerTimingStageLabel } from '../../providerPresentation'
import { COMPLETED_PHASES, SETTLED_PHASES } from '../../domain/workflowStates'
import { taskStatus } from '../../domain/taskPresentation'
import { stageElapsedSeconds, STOPPED_TIMING_STATUSES, taskTimingStages, type TaskTimingStage } from '../../domain/taskTimingPresentation'
import { cutflowPresentation, multiCutflowPresentation } from '../../domain/cutflowPresentation'
import { cutflowElapsedSeconds, type CutflowTimingStatus } from '../../domain/cutflowTiming'
import { timeBeforeStage, timeBreakdown, type TimeWindow } from '../../domain/taskTimeBreakdown'

export type CutflowTimelineState = { enabled: boolean; handoffs: CutflowHandoff[] }

export function TaskElapsed({ task }: { task: Pick<Task, 'phase' | 'createdAt' | 'updatedAt'> }) {
  const isTerminal = SETTLED_PHASES.has(task.phase)
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (isTerminal) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [isTerminal])
  return <>{formatElapsed(task.createdAt, isTerminal ? task.updatedAt : now)}</>
}

const TASK_TIMING_STAGE_META: Array<{
  key: TaskTimingStage['key']
  category: 'system' | 'user'
}> = [
  { key: 'SOURCE_UPLOAD', category: 'system' },
  { key: 'ANALYSIS', category: 'system' },
  { key: 'CONFIRMATION', category: 'user' },
  { key: 'MATERIALIZATION', category: 'system' },
  { key: 'GENERATION', category: 'system' },
  { key: 'DEPTH_EXTRACTION', category: 'system' },
  { key: 'REPLACEMENT', category: 'system' },
  { key: 'DOWNLOAD', category: 'system' },
]

function taskTimingStageShortLabel(key: TaskTimingStage['key']): string {
  if (key === 'SOURCE_UPLOAD') return '素材'
  if (key === 'ANALYSIS') return '分析'
  if (key === 'CONFIRMATION') return '确认'
  if (key === 'MATERIALIZATION') return '上传'
  if (key === 'GENERATION') return '生成'
  if (key === 'DEPTH_EXTRACTION') return '深度提取'
  if (key === 'REPLACEMENT') return '人物替换'
  return '下载'
}

function formatCompactDurationSeconds(value: number): string {
  const seconds = Math.max(0, Math.floor(value))
  if (seconds < 60) return `${seconds}秒`
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60)
    const remainder = seconds % 60
    return remainder ? `${minutes}分${remainder}秒` : `${minutes}分钟`
  }
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return minutes ? `${hours}时${minutes}分` : `${hours}小时`
}

export function TaskTimingTimeline({ task, cutflowStates = {}, multiCutflows = [] }: { task: Task; cutflowStates?: Record<string, CutflowTimelineState>; multiCutflows?: MultiCutflowTask[] }) {
  const timingStages = taskTimingStages(task)
  const stagesByKey = new Map(timingStages.map((stage) => [stage.key, stage]))
  const hasActiveStage = timingStages.some((stage) => stage.status === 'ACTIVE')
  const hasActiveCutflow = multiCutflows.some((batch) => multiCutflowPresentation(batch).active)
    || Object.values(cutflowStates).some((state) => state.handoffs.some((handoff) => cutflowPresentation(handoff).active))
  const hasPendingCutflow = multiCutflows.length === 0 && task.variants.some((variant) => variant.downloadArtifactId
    && cutflowStates[variant.id]?.enabled
    && !cutflowStates[variant.id]?.handoffs?.some((handoff) => handoff.sourceArtifactId === variant.downloadArtifactId))
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!hasActiveStage && !hasActiveCutflow && !hasPendingCutflow) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [hasActiveStage, hasActiveCutflow, hasPendingCutflow])

  const visibleStageKeys = new Set(visibleTaskTimingStageKeys(task.workflowKind, task.settings?.deepVideoPipeline === 'depth-first-v1', task.settings?.deepVideoPipeline === 'depth-input-v1'))
  // Production timeline focuses on meaningful creative work. Upload remains recorded in backend, not shown here.
  if (task.workflowKind !== 'DEEP_VIDEO') visibleStageKeys.add('ANALYSIS')
  visibleStageKeys.delete('SOURCE_UPLOAD')
  visibleStageKeys.delete('MATERIALIZATION')
  const stages = TASK_TIMING_STAGE_META.filter(({ key }) => visibleStageKeys.has(key)).map((meta) => ({
    ...meta,
    stage: stagesByKey.get(meta.key) ?? {
      key: meta.key,
      status: 'PENDING' as const,
      durationSeconds: null,
      activeStartedAt: null,
      attemptCount: null,
    },
  }))
  const cutflowVariants = task.variants.filter((variant) => variant.downloadArtifactId)
  const cutflowEntries = cutflowVariants.map((variant) => {
    const state = cutflowStates[variant.id]
    return { variantId: variant.id, state, handoff: state?.handoffs.find((handoff) => handoff.sourceArtifactId === variant.downloadArtifactId) ?? null }
  })
  // An unfinished result must not be hidden by another variant's completed edit.
  const cutflowEntry = cutflowEntries.find(({ handoff }) => handoff && cutflowPresentation(handoff).active)
    ?? cutflowEntries.find(({ handoff }) => handoff && handoff.status !== 'PENDING_REVIEW')
    ?? cutflowEntries.find(({ handoff }) => !handoff)
    ?? cutflowEntries[0]
    ?? null
  const latestMultiCutflow = [...multiCutflows].sort((left, right) => (
    Date.parse(right.updatedAt) - Date.parse(left.updatedAt)
  ))[0] ?? null
  const latestMultiCutflowPresentation = latestMultiCutflow
    ? multiCutflowPresentation(latestMultiCutflow)
    : null
  const isMergedEdit = Boolean(latestMultiCutflow && latestMultiCutflow.sources.length > 1)
  const cutflowState = cutflowEntry?.state
  const latestCutflow = cutflowEntry?.handoff ?? null
  const cutflowPresentationState = latestCutflow ? cutflowPresentation(latestCutflow) : null
  const multiStatus = latestMultiCutflow
    ? latestMultiCutflow.status === 'FAILED' ? 'FAILED'
      : latestMultiCutflow.status === 'CANCELED' ? 'CANCELLED'
        : latestMultiCutflowPresentation?.active ? 'ACTIVE'
          : latestMultiCutflowPresentation?.completed ? 'COMPLETED' : 'UNKNOWN'
    : null
  const cutflowStatus: CutflowTimingStatus = multiStatus ?? (latestCutflow
    ? latestCutflow.status === 'FAILED' ? 'FAILED'
      : latestCutflow.status === 'CANCELED' ? 'CANCELLED'
        : cutflowPresentationState?.active ? 'ACTIVE' : latestCutflow.status === 'PENDING_REVIEW' && latestCutflow.outputs.length ? 'COMPLETED' : 'UNKNOWN'
    : 'PENDING')
  const cutflowLabel = multiStatus
    ? multiStatus === 'ACTIVE' ? (isMergedEdit ? '合并剪辑中' : '剪辑中')
      : multiStatus === 'COMPLETED' ? (isMergedEdit ? '合并剪辑完成' : '剪辑完成')
        : multiStatus === 'FAILED' ? (isMergedEdit ? '合并剪辑失败' : '剪辑失败')
          : multiStatus === 'CANCELLED' ? (isMergedEdit ? '合并剪辑已取消' : '剪辑已取消')
            : (isMergedEdit ? '合并剪辑待核对' : '剪辑待核对')
    : latestCutflow
    ? cutflowPresentationState?.label ?? '剪辑状态待确认'
    : cutflowState?.enabled === false ? '剪辑未启用' : '待送剪辑'
  const cutflowStatusText = multiStatus
    ? latestMultiCutflowPresentation?.label ?? '状态待确认'
    : latestCutflow
    ? cutflowPresentationState?.label ?? '状态待确认'
    : cutflowState?.enabled === false ? '未启用' : '可送剪辑'
  const showCutflowHeading = Boolean(latestMultiCutflow || latestCutflow) && !timingStages.some((stage) => stage.status === 'ACTIVE' || STOPPED_TIMING_STATUSES.has(stage.status))
  const completedSources = new Set(cutflowEntries
    .filter(({ handoff }) => handoff?.status === 'PENDING_REVIEW' && handoff.outputs.length)
    .map(({ variantId }) => variantId))
  if (latestMultiCutflow?.outputs.length) {
    for (const source of latestMultiCutflow.sources) completedSources.add(source.variantId)
  }
  const completedEdits = [...completedSources].filter(Boolean).length
  const activeStage = stages.find(({ stage }) => stage.status === 'ACTIVE')
  const stoppedStage = stages.find(({ stage }) => STOPPED_TIMING_STATUSES.has(stage.status))
  const currentStage = activeStage ?? stoppedStage
  const stoppedLabel = stoppedStage?.stage.status === 'UNKNOWN' ? '本地处理已中断 · 云端状态待确认'
    : stoppedStage?.stage.status === 'CANCELLED' ? '已取消 · 已停止计时'
      : stoppedStage?.stage.status === 'FAILED' ? '处理失败 · 已停止计时'
        : '处理已中断 · 已停止计时'
  const editingDurations = latestMultiCutflow
    ? [cutflowElapsedSeconds(latestMultiCutflow, latestMultiCutflow.status === 'UNKNOWN' ? 'UNKNOWN' : cutflowStatus, now)]
    : cutflowEntries.map(({ handoff }) => {
      if (!handoff) return null
      const state: CutflowTimingStatus = handoff.status === 'FAILED' ? 'FAILED'
        : handoff.status === 'CANCELED' ? 'CANCELLED'
          : cutflowPresentation(handoff).active ? 'ACTIVE'
            : handoff.status === 'PENDING_REVIEW' && handoff.outputs.length ? 'COMPLETED' : 'UNKNOWN'
      return cutflowElapsedSeconds(handoff, state, now)
    })
  const editingHasTiming = editingDurations.some((duration) => duration !== null)
  // A multi-source cut supersedes per-variant handoffs: count a batch only once.
  const editingSeconds = editingDurations.reduce<number>((total, duration) => total + (duration ?? 0), 0)
  const videoSeconds = stages.reduce((total, item) => {
    if (item.category !== 'system') return total
    return total + (stageElapsedSeconds(item.stage, now) ?? 0)
  }, 0)
  // Only visible system stages count: omit source/provider uploads and manual waits.
  const systemSeconds = videoSeconds + editingSeconds
  // Observed intervals are deduplicated: concurrent generations must not
  // inflate processing time. Cloud/CUT intervals may include internal queues.
  const cutflowWindows: TimeWindow[] = latestMultiCutflow
    ? [{
        startedAt: latestMultiCutflow.createdAt,
        endedAt: cutflowStatus === 'COMPLETED'
          ? latestMultiCutflow.outputs.map((output) => output.createdAt).sort().at(-1) ?? null
          : ['FAILED', 'CANCELLED'].includes(cutflowStatus) ? latestMultiCutflow.updatedAt : null,
        active: cutflowStatus === 'ACTIVE',
      }]
    : cutflowEntries.flatMap(({ handoff }) => {
        if (!handoff) return []
        return [{
          startedAt: handoff.createdAt,
          endedAt: handoff.status === 'PENDING_REVIEW' && handoff.outputs.length
            ? handoff.outputs.map((output) => output.createdAt).sort().at(-1) ?? null
            : ['FAILED', 'CANCELED'].includes(handoff.status) ? handoff.updatedAt : null,
          active: cutflowPresentation(handoff).active,
        }]
      })
  const processStages = timingStages.filter((stage) =>
    stage.key !== 'SOURCE_UPLOAD' && stage.key !== 'CONFIRMATION' && stage.status !== 'PENDING')
  const stageWindows: TimeWindow[] = processStages.map((stage) => ({
    startedAt: stage.startedAt ?? stage.activeStartedAt,
    endedAt: stage.endedAt ?? stage.stoppedAt,
    active: stage.status === 'ACTIVE',
  }))
  const knownWindows = [...stageWindows, ...cutflowWindows]
  // An old API snapshot without boundaries cannot establish queue duration.
  const hasMissingBoundaries = processStages.some((stage) =>
    (stage.durationSeconds ?? 0) > 0 && !stage.startedAt && !stage.activeStartedAt)
    || cutflowWindows.some((window) => !window.startedAt)
  const waitingForCutflow = !latestMultiCutflow && cutflowVariants.length > 0
    && cutflowEntries.some(({ state, handoff }) => state?.enabled && !handoff)
  const totalRunning = hasActiveStage || hasActiveCutflow || waitingForCutflow || !SETTLED_PHASES.has(task.phase)
  const observedEnds = [
    ...stageWindows.map((window) => window.endedAt),
    ...cutflowWindows.map((window) => window.endedAt),
  ].filter((value): value is string => Boolean(value))
    .map(parseTaskTimestamp).filter(Number.isFinite)
  const latestObservedEnd = observedEnds.length ? Math.max(...observedEnds) : parseTaskTimestamp(task.updatedAt)
  const measured = timeBreakdown(task.createdAt, totalRunning ? now : latestObservedEnd, knownWindows, now)
  const canSeparateWaiting = Boolean(measured && knownWindows.some((window) => window.startedAt) && !hasMissingBoundaries)
  const totalSeconds = measured?.totalSeconds ?? null
  const processingSeconds = canSeparateWaiting ? measured!.processingSeconds : systemSeconds
  const waitingSeconds = canSeparateWaiting ? measured!.gapSeconds : null
  const waitBeforeStage = new Map<TaskTimingStage['key'], number>()
  let previousStageEnd: string | null = null
  for (const { key, stage } of stages) {
    const gap = timeBeforeStage(task.createdAt, previousStageEnd, stage.startedAt)
    if (gap !== null && gap > 0) waitBeforeStage.set(key, gap)
    if (stage.endedAt) previousStageEnd = stage.endedAt
  }
  const lastVideoEnd = [...stages].reverse().find(({ stage }) => stage.endedAt)?.stage.endedAt ?? null
  const cutflowStart = cutflowWindows.map((window) => window.startedAt).filter((value): value is string => Boolean(value)).sort()[0]
  const cutflowWait = cutflowStart ? timeBeforeStage(task.createdAt, lastVideoEnd, cutflowStart) : null

  // 方案确认阶段只存在于 legacy 分析流程；固定模板任务上渲染「0 秒」会和「正在等待最终审核」互相矛盾。
  const confirmationVisible = stages.some(({ key }) => key === 'CONFIRMATION')
  const confirmationStage = confirmationVisible ? stagesByKey.get('CONFIRMATION') : undefined
  const confirmationSeconds = confirmationStage ? stageElapsedSeconds(confirmationStage, now) ?? 0 : 0

  const generationMiniStatus = stagesByKey.get('GENERATION')?.status
    ?? (task.variants.some(variant => variant.downloadArtifactId) ? 'COMPLETED' : 'PENDING')
  const analysisMiniStatus = stagesByKey.get('ANALYSIS')?.status
    ?? (generationMiniStatus !== 'PENDING' ? 'COMPLETED' : 'PENDING')
  const miniStages = [
    { key: 'analysis', label: '分析', status: analysisMiniStatus },
    { key: 'generation', label: '生成', status: generationMiniStatus },
    ...(latestMultiCutflow || cutflowVariants.length
      ? [{ key: 'cutflow', label: '剪辑', status: cutflowStatus }]
      : []),
  ]
  const mainStatus = showCutflowHeading ? cutflowLabel
    : currentStage ? providerTimingStageLabel(currentStage.key, task.generationProvider)
      : taskStatus(task).label
  const miniStatusClass = (status: string) => {
    if (status === 'COMPLETED') return 'is-complete'
    if (status === 'ACTIVE') return 'is-current'
    if (['UNKNOWN', 'INTERRUPTED', 'FAILED', 'CANCELLED'].includes(status)) return 'is-issue'
    return 'is-pending'
  }

  return <section className="task-timing task-timing--condensed" aria-labelledby="task-timing-title">
    <div className="task-timing-compact">
      <div className="task-timing-compact__status">
        <span>制作状态</span>
        <strong id="task-timing-title">{mainStatus}</strong>
        <small>总历时 {totalSeconds !== null ? formatDurationSeconds(totalSeconds) : '未完整记录'}</small>
      </div>
      <ol className="task-timing-compact__steps" aria-label="制作阶段概览">
        {miniStages.map(step => <li key={step.key} className={miniStatusClass(step.status)}>
          <span className="task-timing-compact__mark" aria-hidden="true" />
          <span>{step.label}</span>
        </li>)}
      </ol>
    </div>
    <details className="task-timing-breakdown">
      <summary><span>查看详细耗时与各阶段记录</span><ChevronDown size={15} aria-hidden="true" /></summary>
      <header className="task-timing-header">
      <div>
        <span>{showCutflowHeading ? cutflowStatus === 'ACTIVE' ? '当前阶段' : '剪辑状态' : activeStage ? '当前阶段' : stoppedStage ? '停止阶段' : COMPLETED_PHASES.has(task.phase) ? cutflowStatus === 'PENDING' && cutflowVariants.length ? '视频生成完成' : '任务已完成' : task.phase === 'PARTIALLY_DELIVERABLE' ? '部分结果已就绪' : '执行状态'}</span>
        <h3 id="task-timing-title">
          {showCutflowHeading ? cutflowLabel : currentStage ? providerTimingStageLabel(currentStage.key, task.generationProvider) : taskStatus(task).label}
        </h3>
        {activeStage ? <p>已进行 {formatDurationSeconds(stageElapsedSeconds(activeStage.stage, now) ?? 0)}</p> : stoppedStage ? <p>{stoppedLabel}</p> : null}
      </div>
      <dl className="task-timing-summary" aria-label="任务耗时汇总">
        <div title="从任务提交到当前阶段或最后成片的真实经过时间；不重复累计并行任务"><dt>实际总耗时</dt><dd>{totalSeconds !== null ? formatDurationSeconds(totalSeconds) : '暂无记录'}</dd></div>
        <div title="已记录的阶段占用时间（含后台准备过程，以及尚无法拆分的云端或 CUT 内部排队）；对并行阶段去重"><dt>{stoppedStage && !activeStage ? '停止前处理耗时' : '累计处理耗时'}</dt><dd>{processingSeconds > 0 ? formatDurationSeconds(processingSeconds) : '暂无记录'}</dd></div>
        <div title="各处理阶段之外的等待及衔接间隔，包括本地排队、人工确认；可灵或 CUT 内部排队暂包含在阶段耗时中"><dt>累计等待耗时</dt><dd>{waitingSeconds !== null ? formatDurationSeconds(waitingSeconds) : '待独立记录'}</dd></div>


        {confirmationVisible ? <div title="等待确认视频动作方案的累计时长"><dt>等待方案确认</dt><dd>{formatDurationSeconds(confirmationSeconds)}</dd></div> : null}
      </dl>
    </header>
    <ol
      className="task-timing-stages"
      style={{ gridTemplateColumns: `repeat(${stages.length + (cutflowVariants.length || latestMultiCutflow ? 1 : 0)}, minmax(0, 1fr))` }}
    >
      {stages.map(({ key, category, stage }, index) => {
        const elapsed = stageElapsedSeconds(stage, now)
        const analysisUnrecorded = key === 'ANALYSIS' && stage.status === 'PENDING'
          && (['ACTIVE', 'COMPLETED'].includes(stagesByKey.get('GENERATION')?.status ?? '') || COMPLETED_PHASES.has(task.phase))
        const statusText = analysisUnrecorded ? '未单独计时' : stage.status === 'ACTIVE'
          ? formatCompactDurationSeconds(elapsed ?? 0)
          : stage.status === 'COMPLETED'
            ? elapsed === null ? '已完成' : formatCompactDurationSeconds(elapsed)
            : STOPPED_TIMING_STATUSES.has(stage.status)
              ? `${stage.status === 'UNKNOWN' ? '待核实' : stage.status === 'CANCELLED' ? '已取消' : stage.status === 'FAILED' ? '失败' : '已中断'}${elapsed === null ? '' : ` · ${formatCompactDurationSeconds(elapsed)}`}`
              : stoppedStage && !activeStage ? '未执行' : '待执行'
        const fullLabel = key === 'ANALYSIS' ? '素材分析' : providerTimingStageLabel(key, task.generationProvider)
        return <li key={key} className={`task-timing-stage is-${analysisUnrecorded ? 'unrecorded' : stage.status.toLowerCase()}`} aria-label={`${fullLabel}，${analysisUnrecorded ? '未单独记录' : category === 'user' ? '用户等待' : '系统处理'}，${statusText}`} aria-current={stage.status === 'ACTIVE' ? 'step' : undefined}>
          <span className="task-timing-marker" aria-hidden="true">
            {analysisUnrecorded ? <CircleHelp size={15} /> : stage.status === 'COMPLETED' ? <Check size={15} /> : stage.status === 'ACTIVE' ? <LoaderCircle className="spin" size={15} /> : stage.status === 'UNKNOWN' ? <CircleHelp size={15} /> : stage.status === 'INTERRUPTED' ? <Pause size={15} /> : stage.status === 'CANCELLED' ? <X size={15} /> : stage.status === 'FAILED' ? <AlertTriangle size={15} /> : index + 1}
          </span>
          <span className="task-timing-copy">
            <strong><span className="task-timing-label-full">{fullLabel}</span><span className="task-timing-label-short">{taskTimingStageShortLabel(key)}</span></strong>
            <small>{analysisUnrecorded ? '未单独记录' : category === 'user' ? '用户等待' : '系统处理'}{stage.attemptCount && stage.attemptCount > 1 ? ` · 共 ${stage.attemptCount} 次` : ''}{waitBeforeStage.has(key) ? ` · 前置间隔 ${formatCompactDurationSeconds(waitBeforeStage.get(key)!)}` : ''}</small>
          </span>
          <span className="task-timing-duration">{statusText}</span>
        </li>
      })}
      {cutflowVariants.length || latestMultiCutflow ? <li className={`task-timing-stage task-timing-stage--cutflow is-${cutflowStatus.toLowerCase()}`} aria-label={`剪辑，${cutflowStatusText}`} aria-current={cutflowStatus === 'ACTIVE' ? 'step' : undefined}>
        <span className="task-timing-marker" aria-hidden="true">{cutflowStatus === 'COMPLETED' ? <Check size={15} /> : cutflowStatus === 'ACTIVE' ? <LoaderCircle className="spin" size={15} /> : cutflowStatus === 'FAILED' ? <AlertTriangle size={15} /> : cutflowStatus === 'CANCELLED' ? <X size={15} /> : cutflowStatus === 'UNKNOWN' ? <CircleHelp size={15} /> : stages.length + 1}</span>
        <span className="task-timing-copy"><strong><span className="task-timing-label-full">{isMergedEdit ? '合并剪辑' : '剪辑'}</span><span className="task-timing-label-short">剪辑</span></strong><small>{latestMultiCutflow ? isMergedEdit ? `${latestMultiCutflow.sources.length} 条原片合并` : '单片剪辑' : cutflowVariants.length > 1 ? `${completedEdits}/${cutflowVariants.length} 个原片已剪辑` : latestCutflow ? '剪辑任务' : '可在下方送入剪辑'}{cutflowWait && cutflowWait > 0 ? ` · 前置间隔 ${formatCompactDurationSeconds(cutflowWait)}` : ''}</small></span>
        <span className="task-timing-duration">{cutflowLabel}{editingHasTiming ? ` · ${formatCompactDurationSeconds(editingSeconds)}` : ''}</span>
      </li> : null}
    </ol>
    </details>
  </section>
}
