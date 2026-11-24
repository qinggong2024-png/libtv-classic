import { errorMessage } from '../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { AlertTriangle, ArrowLeft, Check, ChevronRight, LoaderCircle, MoreHorizontal, Play, RefreshCw, RotateCcw, Scissors, X, ZoomIn } from 'lucide-react'
import { useEffect, useState } from 'react'
import { analyzeTask, cancelTask, confirmTask, runPromptAssistant, saveTaskPrompts, getAutoCutflowDispatchStatus, type AutoCutflowDispatchStatus } from '../../api'
import { type Asset, type Task, type Variant, type VariantKind } from '../../types'
import { providerPresentation, variantExecutionMessage } from '../../providerPresentation'
import { ACTIVE_PHASES, RESULT_PHASES } from '../../domain/workflowStates'
import { taskHasActionableReason } from '../../domain/statusPresentation'
import { formatTime, isAnalysisFailure, isWaveSpeedBalanceFailure, taskFailureMessage, taskRecoveryHeadline, taskStatus, variantDisplayName, variantKindDisplayName, variantKindShortLabel } from '../../domain/taskPresentation'
import { VariantFeedback } from './VariantFeedback'
import { DepthVideoResult } from './DepthVideoResult'
import { CutflowControl } from './CutflowControl'
import { TaskCutflowActivity, TaskCutflowDeliveries } from './TaskCutflowSections'
import { TaskAdaptiveProgress } from './TaskAdaptiveProgress'
import { CutflowSourceToggle } from './CutflowSourceToggle'
import { MultiCutBasket } from './MultiCutBasket'
import { useMultiCutBasket, type MultiCutBasketController } from './useMultiCutBasket'
import { cutflowPresentation, multiCutflowPresentation } from '../../domain/cutflowPresentation'
import { AuthenticatedImage } from '../../shared/components/Media'
import { TaskAssetPreview } from '../../shared/components/TaskAssetPreview'
import { TaskTimingTimeline, type CutflowTimelineState } from '../../shared/components/TaskTiming'
import { ProviderBadge, TaskStatusBadge } from '../../shared/components/Badges'
import { type AnalysisResult, taskPrompt } from './productionTypes'
import { type PromptRevisionMode, AnalysisProgress, HistoricalPrompt, PlanPanel, VariantStatus } from './WorkspacePanels'
import { GenerationCostBadge } from '../../shared/components/GenerationCostBadge'
import { selectedVideoSeconds } from '../../generationBudget'
import { cutflowDispatchPresentation } from './cutflowDispatchPresentation'
import { autoCutflowCandidate, autoCutflowWaitDelayed, partialAutoCutflowKey } from '../../domain/autoCutflowWaiting'
import { taskTimingStoppedAt } from '../../domain/taskTimingPresentation'
import { TaskDetailDisclosure } from './TaskDetailDisclosure'
import './taskDetailLayout.css'
import './taskTimingRail.css'
import './taskDetailA.css'

const motionAwareScroll = (): ScrollBehavior => window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'

// A submitted mode is not proof of which Sol compiler actually planned a task.
// Older tasks and non-OOTD routes may retain a default directorMode.
const DIRECTOR_COMPILER_LABELS: Record<string, string> = {
  'allwe-ootd-sol-simple-v1': '遵循参考图',
  'allwe-ootd-sol-cross-scene-b-v1': '智能分镜',
  'allwe-ootd-sol-dynamic-display-v1': '智能分镜',
}

function recordedDirectorMode(task: Task): string | null {
  if (task.workflowKind !== 'FIXED_TEMPLATE' || task.settings?.generationMode !== 'automatic') return null
  const compiler = task.compiledPlan?.compiler
  // Display only a mode backed by the completed planner record. Never infer a
  // historical or pending task's actual mode from the submitted default.
  return typeof compiler === 'string' ? DIRECTOR_COMPILER_LABELS[compiler] ?? null : null
}

type WorkspaceProps = Omit<Parameters<typeof ProductionWorkspaceBody>[0], 'cutflowBasket'> & { sharedBasket?: MultiCutBasketController }

export function ProductionWorkspace(props: WorkspaceProps) {
  if (props.sharedBasket) return <ProductionWorkspaceBody {...props} cutflowBasket={props.sharedBasket} />
  return <StandaloneProductionWorkspace {...props} />
}

function StandaloneProductionWorkspace(props: WorkspaceProps) {
  const basket = useMultiCutBasket({
    tasks: props.tasks ?? [props.task],
    canWrite: props.canGenerate ?? true,
    enabled: props.multiCutReady ?? false,
    storageKey: props.basketStorageKey,
    onSubmitted: props.onChanged,
  })
  return <>
    <ProductionWorkspaceBody {...props} cutflowBasket={basket} />
    {props.multiCutReady ? <MultiCutBasket basket={basket} onPlay={(taskId, variantId) => {
      const sourceTask = (props.tasks ?? [props.task]).find(item => item.id === taskId)
      const variant = sourceTask?.variants.find(item => item.id === variantId)
      if (sourceTask && variant) {
        if (props.onPlaySource) props.onPlaySource(sourceTask, variant)
        else if (sourceTask.id === props.task.id) props.onPlay(variant)
      }
    }} /> : null}
  </>
}

function ProductionWorkspaceBody({ task, cutflowBasket, onClose, onChanged, onRemix, onReuse, onDepthReuse, onPlay, onFeedbackChanged, focusVariantId = null, canGenerate = true, multiCutReady = false }: { canGenerate?: boolean; task: Task; tasks?: Task[]; basketStorageKey?: string; cutflowBasket: MultiCutBasketController; onClose: () => void; onChanged: () => Promise<void>; onDepthReuse?: () => void; onRemix: () => void; onReuse: (variants: VariantKind[]) => void; onPlay: (variant: Variant) => void; onPlaySource?: (sourceTask: Task, variant: Variant) => void; onFeedbackChanged?: (task: Task) => void; focusVariantId?: string | null; multiCutReady?: boolean }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<{ asset: Asset; label: string } | null>(null)
  const reusableVariants = task.variants.filter((variant) => variant.promptText.trim())
  const [reuseKinds, setReuseKinds] = useState<VariantKind[]>(() => reusableVariants.map((variant) => variant.kind))
  const [confirmationKinds, setConfirmationKinds] = useState<VariantKind[]>(() => task.variants.map((variant) => variant.kind))
  const [promptDrafts, setPromptDrafts] = useState(() => ({
    stable: taskPrompt(task, 'STABLE'),
    dynamic: taskPrompt(task, 'DYNAMIC'),
  }))
  const [promptDirty, setPromptDirty] = useState<Record<VariantKind, boolean>>({ STABLE: false, DYNAMIC: false })
  const [revisionInstructions, setRevisionInstructions] = useState<Record<VariantKind, string>>({ STABLE: '', DYNAMIC: '' })
  const [revisionMessages, setRevisionMessages] = useState<Record<VariantKind, string>>({ STABLE: '', DYNAMIC: '' })
  const [cutflowStates, setCutflowStates] = useState<Record<string, CutflowTimelineState>>({})
  const [historyTab, setHistoryTab] = useState<'versions' | 'records'>('records')
  const multiSourceCutflows = cutflowBasket.relatedMultiSourceTasksForTask(task.id)
  const singleSourceCutflows = task.variants.flatMap(variant => cutflowBasket.singleSourceVersionsFor(variant.id))
  const editingBatches = [...new Map([...multiSourceCutflows, ...singleSourceCutflows].map(batch => [batch.id, batch])).values()]
  const finishedBatches = editingBatches.filter(batch => batch.outputs.length > 0)
  const legacyOutputCount = Object.values(cutflowStates).flatMap(state => state.handoffs).reduce((sum, handoff) => sum + handoff.outputs.length, 0)
  const hasOlderVersions = finishedBatches.length > 1 || legacyOutputCount > 0
  const activeHistoryTab = hasOlderVersions ? historyTab : 'records'
  const deliveredOutputCount = finishedBatches.reduce((sum, batch) => sum + batch.outputs.length, 0) + legacyOutputCount
  const cutflowInProgress = editingBatches.some(batch => multiCutflowPresentation(batch).active)
    || Object.values(cutflowStates).some(state => state.handoffs.some(handoff => cutflowPresentation(handoff).active))
  const hasCutflowRecord = editingBatches.length > 0 || Object.values(cutflowStates).some(state => state.handoffs.length > 0)
  const awaitingAutoDispatch = Boolean(autoCutflowCandidate(task)) && !hasCutflowRecord
  const [autoDispatchStatus, setAutoDispatchStatus] = useState<AutoCutflowDispatchStatus | null>(null)
  const [autoStatusRefreshKey, setAutoStatusRefreshKey] = useState(0)
  useEffect(() => {
    if (!awaitingAutoDispatch || !multiCutReady) return
    const controller = new AbortController()
    const refresh = async () => {
      try {
        const snapshot = await getAutoCutflowDispatchStatus(task.id, controller.signal)
        if (!controller.signal.aborted) setAutoDispatchStatus(snapshot)
      } catch {
        if (!controller.signal.aborted) setAutoDispatchStatus(null)
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 15_000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [task.id, awaitingAutoDispatch, multiCutReady, autoStatusRefreshKey])
  const verifiedAutoStatus = autoDispatchStatus?.taskId === task.id && awaitingAutoDispatch ? autoDispatchStatus : null
  const latestEditBatch = [...editingBatches].sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))[0]
  const activeEditBatch = editingBatches.find(batch => multiCutflowPresentation(batch).active)
  const legacyHandoffs = Object.values(cutflowStates).flatMap(state => state.handoffs)
  const latestEditFailed = latestEditBatch
    ? latestEditBatch.status === 'FAILED'
    : legacyHandoffs.some(handoff => handoff.status === 'FAILED')
  const dispatchSnapshot = cutflowDispatchPresentation(
    task.settings, hasCutflowRecord, cutflowInProgress, deliveredOutputCount > 0,
    latestEditFailed, !multiCutReady || cutflowBasket.multiTasksLoaded,
    multiCutReady && Boolean(cutflowBasket.multiTaskError),
    autoCutflowWaitDelayed(task, cutflowBasket.multiTasksCheckedAt),
    activeEditBatch ?? latestEditBatch ?? null,
    verifiedAutoStatus,
  )
  const partialReadyIds = task.variants.filter(variant => Boolean(variant.downloadArtifactId)
    && ['SUCCEEDED', 'PARTIAL'].includes(variant.generationStatus)
    && variant.downloadStatus === 'READY'
    && ['PASSED', 'WARNING'].includes(variant.validationStatus)).map(variant => variant.id)
  const partialSalvage = !hasCutflowRecord
    && Boolean(partialAutoCutflowKey(task, partialReadyIds))
  const cutflowDispatch = partialSalvage ? {
    ...dispatchSnapshot,
    headline: '部分原片可用，可继续剪辑',
    detail: `已有 ${partialReadyIds.length}/${task.variants.length} 条原片可用，失败片段不会进入本次剪辑。系统会自动尝试送剪，也可以手动送剪。`,
    stageLabel: '可继续送剪',
    action: 'select' as const,
    actionLabel: '选择成功原片送剪',
    tone: 'attention' as const,
    waiting: false,
  } : dispatchSnapshot
  const allowManualCut = cutflowDispatch.action === 'select' || deliveredOutputCount > 0 || latestEditFailed
  const hasMultipleResults = task.variants.length > 1
  const readyOriginals = task.variants.filter(variant => variant.downloadArtifactId)
  const selectedOriginalsHere = readyOriginals.filter(variant => cutflowBasket.isSelected(variant.id)).length
  const selectedOtherTasks = Math.max(0, cutflowBasket.selection.length - selectedOriginalsHere)

  const selectClipsForCut = () => {
    const available = task.variants.filter(variant => variant.downloadArtifactId)
    const section = document.getElementById('task-original-clips')
    if (section instanceof HTMLDetailsElement) section.open = true
    if (available.length === 1 && multiCutReady) {
      cutflowBasket.add(available[0].id)
    } else {
      section?.scrollIntoView({ behavior: motionAwareScroll(), block: 'start' })
    }
  }
  const cutflowNextAction = () => {
    switch (cutflowDispatch.action) {
      case 'select':
        selectClipsForCut()
        break
      case 'refresh':
        setAutoStatusRefreshKey(value => value + 1)
        cutflowBasket.refreshMultiTasks()
        void onChanged()
        break
      case 'history': {
        const section = document.getElementById('task-cutflow-records')
        setHistoryTab('records')
        if (section instanceof HTMLDetailsElement) section.open = true
        section?.scrollIntoView({ behavior: motionAwareScroll(), block: 'start' })
        break
      }
      case 'view':
        document.getElementById('task-final-video')?.scrollIntoView({ behavior: motionAwareScroll(), block: 'start' })
        break
    }
  }
  const [activeRevision, setActiveRevision] = useState<{ kind: VariantKind; mode: PromptRevisionMode } | null>(null)
  const promptsDirty = promptDirty.STABLE || promptDirty.DYNAMIC
  const analysis = task.analysisResult as AnalysisResult | null
  const provider = providerPresentation(task.generationProvider)
  const isFixedWorkflow = task.workflowKind === 'FIXED_TEMPLATE'
  const isDeepWorkflow = task.workflowKind === 'DEEP_VIDEO'
  const isCustomWorkflow = isFixedWorkflow && task.settings?.generationMode === 'custom'
  const directorModeLabel = recordedDirectorMode(task)
  const stable = task.variants.find((variant) => variant.kind === 'STABLE')
  const dynamic = task.variants.find((variant) => variant.kind === 'DYNAMIC')
  const savePrompts = async (sourceTask = task) => {
    if (!promptDrafts.stable.trim() || !promptDrafts.dynamic.trim()) throw new Error('稳定展示和动态广告的提示词不能为空')
    const changed = await saveTaskPrompts(sourceTask, promptDrafts.stable, promptDrafts.dynamic)
    setPromptDrafts({
      stable: taskPrompt(changed, 'STABLE'),
      dynamic: taskPrompt(changed, 'DYNAMIC'),
    })
    return changed
  }
  const revisePrompt = async (kind: VariantKind, mode: PromptRevisionMode) => {
    const typedInstruction = revisionInstructions[kind].trim()
    if (mode === 'MODIFY' && !typedInstruction) {
      setError('请先用一句话描述想怎么修改')
      return
    }
    const instruction = mode === 'MODIFY'
      ? typedInstruction
      : `重新生成${kind === 'STABLE' ? '稳定展示' : '动态广告'}方案，在不改变服装事实和方案定位的前提下优化动作和镜头。`
    setBusy(true)
    setActiveRevision({ kind, mode })
    setError('')
    setRevisionMessages((current) => ({ ...current, [kind]: '' }))
    try {
      const result = await runPromptAssistant(task, 'REGENERATE', instruction, promptDrafts.stable, promptDrafts.dynamic, kind)
      setPromptDrafts({
        stable: taskPrompt(result.task, 'STABLE'),
        dynamic: taskPrompt(result.task, 'DYNAMIC'),
      })
      setPromptDirty({ STABLE: false, DYNAMIC: false })
      setRevisionMessages((current) => ({ ...current, [kind]: mode === 'MODIFY' ? '已按这句话修改此方案' : '已重新生成此方案' }))
      if (mode === 'MODIFY') setRevisionInstructions((current) => ({ ...current, [kind]: '' }))
      await onChanged()
    } catch (cause) {
      setError(errorMessage(cause, '大模型处理失败，请稍后重试'))
    } finally {
      setBusy(false)
      setActiveRevision(null)
    }
  }
  const confirm = async () => {
    setBusy(true)
    setError('')
    try {
      const currentTask = promptsDirty ? await savePrompts() : task
      await confirmTask(currentTask, confirmationKinds)
      await onChanged()
    } catch (cause) { setError(errorMessage(cause, '确认失败，请稍后重试')) } finally { setBusy(false) }
  }
  const retryAnalysis = async () => {
    setBusy(true)
    setError('')
    try {
      await analyzeTask(task)
      await onChanged()
    } catch (cause) {
      setError(errorMessage(cause, '重新分析启动失败，请稍后重试'))
    } finally { setBusy(false) }
  }
  const cancel = async () => {
    if (!window.confirm(`确定取消此任务吗？已提交到 ${providerPresentation(task.generationProvider).displayName} 的远端任务可能无法撤回。`)) return
    setBusy(true)
    setError('')
    try {
      await cancelTask(task.id)
      await onChanged()
    } catch (cause) {
      setError(errorMessage(cause, '任务取消失败，请稍后重试'))
    } finally { setBusy(false) }
  }
  const isConfirmation = !isFixedWorkflow && !isDeepWorkflow && task.phase === 'AWAITING_CONFIRMATION'
  const isGenerating = ACTIVE_PHASES.has(task.phase) && task.phase !== 'DRAFT' && task.phase !== 'ANALYZING'
  const isCompleted = RESULT_PHASES.has(task.phase)
  const showProductionProgress = ACTIVE_PHASES.has(task.phase)
  const showCutStatus = isCompleted && (!finishedBatches.length || cutflowInProgress || latestEditFailed)
  const canReuse = canGenerate && !isFixedWorkflow && !isDeepWorkflow && isCompleted && task.actionCapabilities.canRegenerate && reusableVariants.length > 0
  const analysisFailure = isAnalysisFailure(task)
  const needsRecovery = task.phase === 'ACTION_REQUIRED'
  const stoppedAt = taskTimingStoppedAt(task)
  const confirmationChoice = confirmationKinds.length === 2
    ? 'BOTH'
    : confirmationKinds[0] ?? task.variants[0]?.kind
  const confirmationLabel = confirmationKinds.length === 2
    ? '全部方案'
    : variantKindDisplayName(confirmationKinds[0] ?? task.variants[0]?.kind ?? 'STABLE')
  useEffect(() => {
    if (!focusVariantId) return
    const frame = window.requestAnimationFrame(() => document.getElementById(`production-variant-${focusVariantId}`)?.scrollIntoView({ behavior: motionAwareScroll(), block: 'center' }))
    return () => window.cancelAnimationFrame(frame)
  }, [focusVariantId])
  const sourceContent = (
    <section className="source-summary"><div className="source-assets">{[...task.assets].sort((a, b) => a.order - b.order).map((asset, index) => {
          const isVideo = asset.role === 'REFERENCE_VIDEO'
          const label = isVideo ? '参考视频' : isDeepWorkflow ? asset.role === 'BACKGROUND_REFERENCE' ? '背景参考图' : '目标参考图' : asset.role === 'PRODUCT' || asset.usedForGeneration === false ? '产品图' : `分镜 ${index + 1}`
          return <figure key={asset.id}><button type="button" className="source-asset-preview" aria-label={`预览${label}：${asset.originalFilename}`} title={`点击预览${label}`} onClick={() => setPreview({ asset, label })}>
            <AuthenticatedImage src={asset.thumbnailUrl} alt={asset.originalFilename} />
            <span className="source-asset-preview-icon" aria-hidden="true">{isVideo ? <Play size={14} /> : <ZoomIn size={14} />}</span>
            <span className="source-asset-label">{label}</span>
          </button></figure>
        })}</div><div className="garment-summary"><span>{isDeepWorkflow ? '本次深度视频设置' : isCustomWorkflow ? '本次自定义设置' : isFixedWorkflow ? '本次自动生成设置' : '服装识别摘要'}</span><strong>{isDeepWorkflow ? (task.settings?.deepVideoMode === 'depth' ? '提取深度视频' : '人物与商品替换 · Seedance 2.0') : isCustomWorkflow ? '自定义提示词 · 默认生产规格' : isFixedWorkflow ? '按所选站点风格制作' : [analysis?.garmentFacts?.category, analysis?.garmentFacts?.mainColor, analysis?.garmentFacts?.fit].filter(Boolean).join(' · ') || '正在整理服装信息'}</strong><p>{isDeepWorkflow ? `${task.settings?.deepVideoPipeline === 'depth-input-v1' ? '已有深度视频 → 人物与商品替换' : task.settings?.deepVideoPipeline === 'depth-first-v1' ? (isCompleted ? '深度提取 → 人物与商品替换' : task.settings?.deepVideoStage === 'replacement' ? '第 2 步：使用深度视频替换人物与商品' : '第 1 步：提取深度视频') : '参考视频驱动'} · ${String(task.settings?.durationSeconds ?? '')} 秒 · ${task.settings?.deepVideoPipeline === 'depth-first-v1' ? '深度视频 + 替换结果' : '1 个结果'}` : isCustomWorkflow ? `提示词原文直达 · ${task.variants.length} 个结果` : isFixedWorkflow ? `动作 ${({ low: '轻柔', standard: '标准', high: '明显' } as Record<string, string>)[String(task.settings?.actionIntensity ?? 'standard')] ?? '按所选设置'} · ${task.variants.length} 个结果` : analysis?.consistencyObservations?.summary ?? '系统将优先保持服装结构、颜色、印花和文字准确。'}</p></div></section>
  )
  const taskTechnicalContent = (
    <div><p>联系管理员时，可提供任务编号，帮助查找这次任务。</p><dl>
            <dt>任务编号</dt><dd>{task.id}</dd>
            <dt>创建时间</dt><dd>{formatTime(task.createdAt)}</dd>
            {stoppedAt ? <><dt>停止时间</dt><dd>{formatTime(stoppedAt)}</dd></> : null}
            {directorModeLabel ? <><dt>编排模式</dt><dd>{directorModeLabel}</dd></> : null}
            <dt>生成服务</dt><dd>{provider.displayLabel}</dd>
            <dt>视频生成状态</dt><dd>{taskStatus(task).label}</dd>
            {taskHasActionableReason(task) ? <><dt>情况说明</dt><dd>{taskFailureMessage(task)}</dd></> : null}
          </dl><div className="prompt-hashes">{task.variants.map((variant) => <span key={variant.id}>
            <strong>{variantDisplayName(variant)} · {providerPresentation(variant.provider || task.generationProvider).displayLabel}</strong>
            <small>{variantExecutionMessage(variant, task.generationProvider, task.phase)}</small>
            {variant.providerTaskId ? <small>生成服务查询编号：{variant.providerTaskId}</small> : null}
          </span>)}</div></div>
  )
  return <div className="workspace-view">
    <section className={isCompleted ? "production-workspace task-flow-completed task-layout-a" : "production-workspace task-layout-a"} aria-labelledby="detail-title">
      <header className="drawer-header">
        <button className="icon-button" onClick={onClose} aria-label="关闭任务详情"><ArrowLeft size={19} /></button>
        <div className="drawer-header-main"><span>任务 / {isConfirmation ? '方案确认' : RESULT_PHASES.has(task.phase) ? '视频结果' : task.phase === 'CANCELLED' ? '已取消' : analysisFailure ? '分析恢复' : needsRecovery ? '异常处理' : '生产进度'}</span><h1 id="detail-title">{task.title}</h1></div>
        <div className="drawer-header-actions"><ProviderBadge provider={task.generationProvider} />{!isCompleted && !showProductionProgress && !needsRecovery && !isConfirmation ? <TaskStatusBadge task={task} /> : null}</div>
      </header>
      {showProductionProgress ? <TaskAdaptiveProgress task={task} /> : null}
      {showCutStatus ? <section className={`task-adaptive-cut task-adaptive-cut--${cutflowDispatch.tone}${cutflowDispatch.action === 'select' ? ' task-adaptive-cut--ready' : ''}`} aria-label="当前剪辑状态" role={cutflowDispatch.tone === 'attention' && cutflowDispatch.action !== 'select' ? 'alert' : 'status'}>
        <span className="task-adaptive-cut__icon" aria-hidden="true">{(cutflowInProgress && cutflowDispatch.tone !== 'attention') || cutflowDispatch.waiting ? <LoaderCircle className="spin" size={20} /> : cutflowDispatch.action === 'select' ? <Scissors size={20} /> : cutflowDispatch.tone === 'attention' ? <AlertTriangle size={20} /> : <Scissors size={20} />}</span>
        <div className="task-adaptive-cut__body">
          <h2>{cutflowDispatch.headline}</h2>
          <p>{activeEditBatch ? (activeEditBatch.errorMessage || `${multiCutflowPresentation(activeEditBatch).label}，系统会自动更新进度。`) : latestEditFailed && latestEditBatch?.errorMessage ? latestEditBatch.errorMessage : partialSalvage ? cutflowDispatch.detail : cutflowDispatch.action === 'select' && task.variants.filter(variant => variant.downloadArtifactId).length === 1 && multiCutReady && !cutflowBasket.selection.length ? '原片已就绪，点击下方按钮即可直接送剪。' : cutflowDispatch.detail}</p>
          <small>原片已就绪 {task.variants.filter(variant => variant.downloadArtifactId).length}/{task.variants.length} · {cutflowDispatch.modeLabel}</small>
        </div>
        {cutflowDispatch.actionLabel && (cutflowDispatch.action !== 'select' || partialSalvage)
          ? <button type="button" className="secondary-button" onClick={cutflowNextAction}>{cutflowDispatch.actionLabel}</button>
          : null}
      </section> : null}
      <div className="drawer-content">
        {analysisFailure ? <section className="analysis-recovery" role="alert">
          <span className="analysis-recovery-icon"><AlertTriangle size={22} /></span>
          <div><span>方案尚未生成</span><h2>{taskStatus(task).label}</h2><p>{taskFailureMessage(task)} 重新分析会继续使用当前素材，并按素材数量重新计算等待时间。</p></div>
          <button className="primary-button" disabled={busy || !canGenerate || !task.actionCapabilities.canRetryAnalysis} onClick={() => void retryAnalysis()}>{busy ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}重新分析方案</button>
        </section> : null}
        {needsRecovery && !analysisFailure ? <section className="analysis-recovery" role="alert">
          <span className="analysis-recovery-icon"><AlertTriangle size={22} /></span>
          <div><span>{isWaveSpeedBalanceFailure(task) ? '账户余额不足' : '任务需要处理'}</span><h2>{task.variants.some(variant => variant.generationStatus === 'SUCCEEDED' && variant.downloadStatus === 'READY' && variant.validationStatus === 'FAILED') ? '生成已完成，成品校验未通过' : task.variants.some(variant => variant.generationStatus === 'SUCCEEDED' && variant.downloadStatus === 'FAILED') ? '生成已完成，成品下载失败' : taskRecoveryHeadline(task)}</h2><p>{taskFailureMessage(task)}</p></div>
          {canGenerate && task.actionCapabilities.canRegenerate && !isWaveSpeedBalanceFailure(task) ? <button className="primary-button" type="button" onClick={onRemix}>使用原素材重新制作</button> : null}
        </section> : null}
        {isCompleted && finishedBatches.length ? <section className="task-detail-section task-detail-primary-output" id="task-final-video" aria-label="最新成片">
          <TaskCutflowDeliveries batches={finishedBatches} sourceLabel={source => {
            const sourceVariant = task.variants.find(variant => variant.id === source.variantId)
            return sourceVariant ? variantDisplayName(sourceVariant) + " · " + source.title : source.title
          }} onViewed={() => cutflowBasket.refreshMultiTasks()} display="latest" />
        </section> : null}
        {isConfirmation ? <>
          <div className="drawer-section-title"><div><span>步骤 2 / 3</span><h2>确认视频方案</h2></div><p>确认后将保存当前提示词，并按这些内容发送到 {provider.displayLabel}。</p></div>
          <div className={`plans-grid${task.variants.length === 1 ? ' plans-grid--single' : ''}`}>
            {stable ? <PlanPanel kind="STABLE" plan={analysis?.stablePlan} variant={stable} risks={analysis?.riskFlags ?? []} prompt={promptDrafts.stable} promptDirty={promptDirty.STABLE} disabled={busy || !canGenerate} revisionInstruction={revisionInstructions.STABLE} revisionMessage={revisionMessages.STABLE} activeRevisionMode={activeRevision?.kind === 'STABLE' ? activeRevision.mode : null} onPromptChange={(value) => { setPromptDrafts((current) => ({ ...current, stable: value })); setPromptDirty((current) => ({ ...current, STABLE: true })) }} onRevisionInstructionChange={(value) => setRevisionInstructions((current) => ({ ...current, STABLE: value }))} onPromptRevision={(mode) => void revisePrompt('STABLE', mode)} /> : null}
            {dynamic ? <PlanPanel kind="DYNAMIC" plan={analysis?.dynamicPlan} variant={dynamic} risks={analysis?.riskFlags ?? []} prompt={promptDrafts.dynamic} promptDirty={promptDirty.DYNAMIC} disabled={busy || !canGenerate} revisionInstruction={revisionInstructions.DYNAMIC} revisionMessage={revisionMessages.DYNAMIC} activeRevisionMode={activeRevision?.kind === 'DYNAMIC' ? activeRevision.mode : null} onPromptChange={(value) => { setPromptDrafts((current) => ({ ...current, dynamic: value })); setPromptDirty((current) => ({ ...current, DYNAMIC: true })) }} onRevisionInstructionChange={(value) => setRevisionInstructions((current) => ({ ...current, DYNAMIC: value }))} onPromptRevision={(mode) => void revisePrompt('DYNAMIC', mode)} /> : null}
          </div>
        </> : null}
        {isGenerating || RESULT_PHASES.has(task.phase) || task.phase === 'CANCELLED' || (task.phase === 'ACTION_REQUIRED' && !analysisFailure) ? <>
          {!showProductionProgress && !needsRecovery && !isCompleted ? <div className="drawer-section-title"><div><span>{task.settings?.deepVideoPipeline === 'depth-first-v1' ? '深度提取 → Seedance 2.0 替换' : '生成详情'} · {provider.displayLabel}</span><h2>{RESULT_PHASES.has(task.phase) ? '视频制作与成片' : task.phase === 'CANCELLED' ? '任务已取消' : needsRecovery ? `${provider.displayLabel} 任务需要处理` : `${provider.displayLabel} 生成进度`}</h2></div></div> : null}
          {task.settings?.deepVideoPipeline === 'depth-input-v1' ? <p className="video-recreation-note">深度视频已就绪 · 本次直接替换，不重复提取深度。</p> : null}
          {!task.depthVideoResult && task.settings?.deepVideoMode === 'depth' && canGenerate && task.variants.some((variant) => variant.downloadArtifactId) && onDepthReuse ? <button className="secondary-button" onClick={onDepthReuse}><RotateCcw size={16} />用于人物与商品替换</button> : null}
          {task.depthVideoResult ? <DepthVideoResult result={task.depthVideoResult} title={task.title} onReuse={canGenerate ? onDepthReuse : undefined} /> : null}
          <section className="task-detail-section task-a-originals" id="task-original-clips">
            <div className="task-detail-section__heading"><h2>原片与送剪</h2><p>已就绪 {readyOriginals.length}/{task.variants.length} · {finishedBatches.length ? '可再次选择原片送剪' : allowManualCut ? '预览原片后选择送剪' : '查看原片与制作状态'}</p></div>
          <div className={hasMultipleResults ? 'task-original-gallery' : 'task-original-standard'}>
          <div className={`variant-status-list variant-status-list--production${hasMultipleResults ? ' variant-status-list--gallery' : ' variant-status-list--single'}`}>{task.variants.map((variant, variantIndex) => {
            const currentHandoff = cutflowStates[variant.id]?.handoffs.find((handoff) => handoff.sourceArtifactId === variant.downloadArtifactId)
            const variantMultiSourceCutflows = multiSourceCutflows.filter(batch => batch.sources.some(source => source.variantId === variant.id))
            const latestVariantMultiSourceCutflow = [...variantMultiSourceCutflows].sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt))[0] ?? null
            const multiSourceInProgress = latestVariantMultiSourceCutflow ? multiCutflowPresentation(latestVariantMultiSourceCutflow).active : false
            const locked = Boolean(currentHandoff && cutflowPresentation(currentHandoff).active) || multiSourceInProgress || cutflowBasket.isInProgress(variant.id)
            const previouslyCut = Boolean(currentHandoff || cutflowBasket.isPreviouslyCut(variant.id))
            return <section className={`production-video-result${hasMultipleResults ? ' production-video-result--tile' : ''}${cutflowBasket.isSelected(variant.id) ? ' production-video-result--selected' : ''}`} id={`production-variant-${variant.id}`} key={variant.id} aria-label={`${variantDisplayName(variant)}原片与剪辑`}>
              {!hasMultipleResults ? <header className="production-result-heading">
                <div><span>{variantDisplayName(variant)}</span><h3>原片</h3></div>

              </header> : null}
              <div className="task-detail-original"><VariantStatus task={task} variant={variant} onChanged={onChanged} onPlay={() => onPlay(variant)} tile={hasMultipleResults} tileLabel={`原片 ${String(variantIndex + 1).padStart(2, '0')}`} prominentPreview={isCompleted && Boolean(variant.downloadArtifactId)} sourceAction={variant.downloadArtifactId && multiCutReady && canGenerate && !awaitingAutoDispatch ? (task.variants.filter(item => item.downloadArtifactId).length === 1 && !cutflowBasket.selection.length
                ? <button type="button" className="primary-button generation-priced-button task-direct-cut-button" disabled={locked || cutflowBasket.submitting} onClick={() => void cutflowBasket.submitOne(variant.id)}>
                    {cutflowBasket.submitting ? <LoaderCircle className="spin" size={16} /> : <Scissors size={16} />}
                    <span>{cutflowBasket.submitting ? '正在送剪' : previouslyCut ? '再次送剪' : '直接送剪'}</span>
                    <GenerationCostBadge category="cutflowPriceUsd" units={1} />
                  </button>
                : <CutflowSourceToggle
                  selected={cutflowBasket.isSelected(variant.id)}
                  disabled={locked || (!cutflowBasket.isSelected(variant.id) && cutflowBasket.atCapacity)}
                  disabledLabel={locked
                    ? currentHandoff ? cutflowPresentation(currentHandoff).label : latestVariantMultiSourceCutflow ? multiCutflowPresentation(latestVariantMultiSourceCutflow).label : '剪辑中'
                    : `已达 ${cutflowBasket.maxSources} 条上限`}
                  actionLabel={previouslyCut ? '重新剪辑' : '选择送剪'}
                  hint={previouslyCut ? '创建一个新剪辑版本，旧成片继续保留' : '可与同任务其他原片一起送剪'}
                  onToggle={() => cutflowBasket.toggle(variant.id)}
                />) : undefined} /></div>
              {variant.downloadArtifactId && onFeedbackChanged ? <VariantFeedback task={task} variant={variant} onFeedbackChanged={onFeedbackChanged} /> : null}
            </section>
          })}</div>
          {hasMultipleResults && !awaitingAutoDispatch ? <aside className="task-original-gallery__sidebar" aria-label="批量送剪操作">
            <div className="task-original-gallery__eyebrow"><Scissors size={15} />下一步操作</div>
            <h3>{multiCutReady && canGenerate ? '批量送剪' : '原片已生成'}</h3>
            <p>{multiCutReady && canGenerate
              ? '勾选上方原片加入剪辑篮。多条原片会按剪辑篮中的顺序合成。'
              : '可逐条查看和下载原片；剪辑入口暂不可用。'}</p>
            <div className="task-original-gallery__selection" aria-live="polite">
              <span>{selectedOriginalsHere ? `本任务已选 ${selectedOriginalsHere}/${readyOriginals.length} 条` : `本任务 ${readyOriginals.length} 条原片已就绪`}</span>
              {selectedOtherTasks > 0 ? <small>另有其他任务的 {selectedOtherTasks} 条已加入剪辑篮，将一同提交</small> : null}
            </div>
            <button type="button" className="primary-button task-original-gallery__submit"
              disabled={!multiCutReady || !canGenerate || selectedOriginalsHere === 0 || !cutflowBasket.canSubmit}
              onClick={() => void cutflowBasket.submit()}
              title={selectedOriginalsHere === 0 ? '请先选择本任务原片' : cutflowBasket.submitHint ?? '使用当前剪辑篮顺序送剪'}>
              {cutflowBasket.submitting ? <LoaderCircle size={16} className="spin" /> : <Scissors size={16} />}
              <span>{cutflowBasket.submitting ? '正在送剪' : cutflowBasket.selection.length
                ? `送剪已选 ${cutflowBasket.selection.length} 条` : '选择原片后送剪'}</span>
            </button>
            {cutflowBasket.selection.length ? <p className="task-original-gallery__footnote">剪辑顺序可在下方剪辑篮调整，此处提交与剪辑篮使用同一份选择。</p> : <p className="task-original-gallery__footnote">选中视频后即可送剪，不会改变原片文件。</p>}
            {selectedOriginalsHere > 0 && cutflowBasket.submitHint ? <small className="task-original-gallery__warning" role="status">{cutflowBasket.submitHint}</small> : null}
          </aside> : null}
          </div></section>
        </> : null}
        <TaskTimingTimeline task={task} cutflowStates={cutflowStates} multiCutflows={editingBatches} />
        <TaskDetailDisclosure title="生成参数" hint="参考素材与生成设置 · 提示词">
          {sourceContent}
          {isCompleted ? <section className="task-a-prompts" aria-label="本次提示词与历史方案"><h3>本次提示词与历史方案</h3>
                {reusableVariants.length ? <div className={`history-prompts${reusableVariants.length === 1 ? ' history-prompts--single' : ''}`}>
                  {reusableVariants.map((variant) => <HistoricalPrompt key={variant.id} task={task} variant={variant} selected={reuseKinds.includes(variant.kind)} />)}
                </div> : <p className="task-materials__empty">本次任务没有已保存的提示词。</p>}
          </section> : null}
        </TaskDetailDisclosure>
        <TaskDetailDisclosure title="制作记录" hint="剪辑记录 · 历史版本" id="task-cutflow-records">
          {!hasOlderVersions && editingBatches.length === 1 ? <section className="task-history-workspace task-history-workspace--compact" aria-label="剪辑记录">
            <h3>剪辑记录（1）</h3>
            <div className="task-history-workspace__compact-content"><TaskCutflowActivity batches={editingBatches} inlineHistory /></div>
          </section> : <section className="task-history-workspace" aria-label="剪辑与成片">
            <div className="task-history-workspace__heading">
              <h2>剪辑记录与历史版本</h2>
              <span>最新成片已在上方展示</span>
            </div>
            <div className="task-history-workspace__tabs" role="tablist" aria-label="剪辑与成片切换">
              <button id="task-tab-records" type="button" role="tab" aria-selected={activeHistoryTab === 'records'} aria-controls="task-panel-records" className={activeHistoryTab === 'records' ? 'is-active' : ''} onClick={() => setHistoryTab('records')}>剪辑记录{editingBatches.length ? `（${editingBatches.length}）` : ''}</button>
              {hasOlderVersions ? <button id="task-tab-versions" type="button" role="tab" aria-selected={activeHistoryTab === 'versions'} aria-controls="task-panel-versions" className={activeHistoryTab === 'versions' ? 'is-active' : ''} onClick={() => setHistoryTab('versions')}>历史成片（{Math.max(0, finishedBatches.length - 1) + (legacyOutputCount > 0 ? 1 : 0)}）</button> : null}
            </div>
            <div className="task-history-workspace__panel" id="task-panel-versions" role="tabpanel" aria-labelledby="task-tab-versions" hidden={activeHistoryTab !== 'versions'}>
              {finishedBatches.length ? <>
                <p className="task-history-workspace__hint">最新成片显示在页面上方，这里保留较早的剪辑版本。</p>
                <TaskCutflowDeliveries batches={finishedBatches} display="history" sourceLabel={source => {
                  const matched = task.variants.find(variant => variant.id === source.variantId)
                  return matched ? variantDisplayName(matched) + ' · ' + source.title : source.title
                }} onViewed={() => cutflowBasket.refreshMultiTasks()} />
              </> : null}
              <div className="task-history-workspace__legacy" hidden={Boolean(multiCutReady && !legacyOutputCount)}>
                {task.variants.some(variant => variant.downloadArtifactId) ? <div className="task-detail-single-history__content">{task.variants.filter(variant => variant.downloadArtifactId).map(variant => <div key={variant.id} className="task-detail-single-entry">
                  <strong>{variantDisplayName(variant)}</strong>
                  <CutflowControl taskId={task.id} variantId={variant.id} artifactId={variant.downloadArtifactId!} siteProfileId={task.siteProfileId} canWrite={canGenerate} allowSubmit={!multiCutReady} revisionTasks={[]} multiSourceTasks={[]} sourceLabel={variantDisplayName(variant)} onStateRead={state => setCutflowStates(current => ({ ...current, [variant.id]: state }))} />
                </div>)}</div> : null}
              </div>
              {!finishedBatches.length && !legacyOutputCount && multiCutReady ? <p className="task-history-workspace__empty">尚无剪辑成片，完成送剪后会显示在这里。</p> : null}
            </div>
            <div className="task-history-workspace__panel" id="task-panel-records" role="tabpanel" aria-labelledby="task-tab-records" hidden={activeHistoryTab !== 'records'}>
              {editingBatches.length ? <TaskCutflowActivity batches={editingBatches} inlineHistory /> : <p className="task-history-workspace__empty">暂时没有新的剪辑记录。</p>}
              {Object.values(cutflowStates).flatMap(state => state.handoffs).filter(handoff => !editingBatches.some(batch => batch.sources.some(source => source.artifactId === handoff.sourceArtifactId))).length ? <details className="task-detail-single-history">
                <summary>旧版单片剪辑状态 <ChevronRight size={15} /></summary>
                <div className="task-detail-single-history__content">{Object.values(cutflowStates).flatMap(state => state.handoffs).filter(handoff => !editingBatches.some(batch => batch.sources.some(source => source.artifactId === handoff.sourceArtifactId))).map(handoff => <p className="task-detail-legacy-status" key={handoff.id}>{cutflowPresentation(handoff).label}</p>)}</div>
              </details> : null}
            </div>
          </section>}

          <section className="technical-details" aria-label="任务编号与技术信息"><h3><MoreHorizontal size={17} />任务详情</h3>{taskTechnicalContent}</section>
        </TaskDetailDisclosure>
        {task.phase === 'ANALYZING' ? <details className="task-detail-timing"><summary>查看分析详情 <ChevronRight size={15} /></summary><AnalysisProgress task={task} /></details> : null}
        {error ? <div className="inline-alert inline-alert--danger" role="alert"><AlertTriangle size={17} />{error}</div> : null}
      </div>
      {(canGenerate && task.actionCapabilities.canRegenerate) || task.actionCapabilities.canCancel || (canGenerate && isConfirmation) ? <footer className={`drawer-footer${canReuse ? ' drawer-footer--reuse' : ''}${isConfirmation ? ' drawer-footer--confirmation' : ''}`}>
        <div className="drawer-footer-actions">
          {task.actionCapabilities.canCancel ? <button className="secondary-button secondary-button--danger" disabled={busy} onClick={() => void cancel()}><X size={16} />取消任务</button> : null}
          {canGenerate && task.actionCapabilities.canRegenerate && !canReuse && !needsRecovery ? <button className="secondary-button" onClick={onRemix}><RotateCcw size={16} />使用相同素材再制作</button> : null}
        </div>
        {canReuse ? <div className="reuse-controls">
          <fieldset>
            <legend>选择复用方案</legend>
            {(['STABLE', 'DYNAMIC'] as VariantKind[]).map((kind) => {
              const available = reusableVariants.some((variant) => variant.kind === kind)
              const checked = reuseKinds.includes(kind)
              return <label key={kind} className={`${checked ? 'is-selected' : ''}${available ? '' : ' is-disabled'}`}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!available}
                  onChange={() => setReuseKinds((current) => checked ? current.filter((item) => item !== kind) : [...current, kind].sort((a) => a === 'STABLE' ? -1 : 1))}
                />
                <span className={`variant-label ${kind === 'STABLE' ? 'variant-label--a' : 'variant-label--b'}`}>{variantKindShortLabel(kind)}</span>
                <span><strong>{variantKindDisplayName(kind)}</strong>{!available ? <small>无历史提示词</small> : null}</span>
              </label>
            })}
          </fieldset>
          <div>
            {!reuseKinds.length ? <small role="alert">请至少勾选一个方案</small> : <small>将复用全部素材及所选提示词，并跳过图片分析</small>}
            <button className="primary-button primary-button--large generation-priced-button" disabled={!reuseKinds.length} onClick={() => onReuse(reuseKinds)}><RotateCcw size={17} /><span>一键复用并生成 {reuseKinds.length || ''} 个结果</span><GenerationCostBadge category="videoPricePerSecondUsd" units={selectedVideoSeconds(task.variants, reuseKinds)} /></button>
          </div>
        </div> : null}
        {canGenerate && isConfirmation ? <div className="confirmation-submit">
          {task.variants.length > 1 ? <fieldset className="confirmation-variant-picker">
            <legend>本次生成</legend>
            <div>
              <label className={confirmationChoice === 'STABLE' ? 'is-selected' : ''}>
                <input type="radio" name={`confirmation-variant-${task.id}`} checked={confirmationChoice === 'STABLE'} disabled={busy} onChange={() => setConfirmationKinds(['STABLE'])} />
                <span>稳定展示</span>
              </label>
              <label className={confirmationChoice === 'BOTH' ? 'is-selected' : ''}>
                <input type="radio" name={`confirmation-variant-${task.id}`} checked={confirmationChoice === 'BOTH'} disabled={busy} onChange={() => setConfirmationKinds(['STABLE', 'DYNAMIC'])} />
                <span>全部方案</span>
              </label>
              <label className={confirmationChoice === 'DYNAMIC' ? 'is-selected' : ''}>
                <input type="radio" name={`confirmation-variant-${task.id}`} checked={confirmationChoice === 'DYNAMIC'} disabled={busy} onChange={() => setConfirmationKinds(['DYNAMIC'])} />
                <span>动态广告</span>
              </label>
            </div>
          </fieldset> : null}
          <button className="primary-button primary-button--large generation-priced-button" disabled={busy} onClick={() => void confirm()}>{busy ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}<span>确认并生成 {confirmationLabel}</span><GenerationCostBadge category="videoPricePerSecondUsd" units={selectedVideoSeconds(task.variants, confirmationKinds)} /></button>
        </div> : null}
      </footer> : null}
    </section>
    {preview ? <TaskAssetPreview key={preview.asset.id} asset={preview.asset} label={preview.label} onClose={() => setPreview(null)} /> : null}
  </div>
}
