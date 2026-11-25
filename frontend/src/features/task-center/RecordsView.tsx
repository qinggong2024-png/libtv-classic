import { ChevronDown, CircleAlert, LayoutGrid, List, ListVideo, LoaderCircle, MoreHorizontal, RefreshCw, Search, X } from 'lucide-react'
import { Fragment, useEffect, useRef, useState } from 'react'
import type { RecordsFilter as AppRecordsFilter } from '../../app/appTypes'
import type { Task, Variant } from '../../types'
import { ACTIVE_PHASES, FAILED_PHASES } from '../../domain/workflowStates'
import { taskCenterState } from '../../domain/taskCenterState'
import { taskHasActionableReason } from '../../domain/statusPresentation'
import { isTaskAttentionAcknowledged, taskAttentionGroup, taskAttentionSignature, type TaskAttentionMap } from '../../domain/taskAttention'
import { hasUnseenCutflowOutput } from '../../domain/cutflowUnread'
import { DataFreshness } from '../../shared/components/DataFreshness'
import { EmptyState } from '../../shared/components/Section'
import { MultiCutBasket } from '../production/MultiCutBasket'
import { CutflowHistory } from '../production/CutflowHistory'
import { useMultiCutBasket, type MultiCutBasketController } from '../production/useMultiCutBasket'
import { TaskClipPickerLauncher } from './TaskClipPickerLauncher'
import { formatTime, taskFailureMessage } from '../../domain/taskPresentation'
import { TaskMediaCover, TaskQuickPreview } from './TaskQuickPreview'
import { taskCutCoverStatus, taskPlayableMedia, taskPrimaryMedia } from './taskCenterMedia'

import { TaskDatePicker } from './TaskDatePicker'
import { localDayKey, parseTaskDateRange, taskCreatedOn, taskDateLabel, type TaskDateRange } from './TaskDateRange'
import './taskCenterCards.css'
import './taskCenterV22.css'
import './taskCenterAlignment.css'
import './TaskDatePicker.css'

export type RecordFilter = AppRecordsFilter

const MANUAL_REFRESH_COOLDOWN_MS = 5000

function TaskCenterCard({ task, basket, canWrite, acknowledged, onDismissAttention, onRestoreAttention, onCancelTask, onOpen, onQuickPlay }: {
  task: Task
  basket: MultiCutBasketController
  canWrite: boolean
  acknowledged: boolean
  onDismissAttention?: () => void
  onRestoreAttention?: () => void
  onCancelTask?: () => Promise<void>
  onOpen: () => void
  onQuickPlay: (task: Task, key: string) => void
}) {

  const [cancelBusy, setCancelBusy] = useState(false)
  const [cancelError, setCancelError] = useState('')
  const cancel = async () => {
    if (!onCancelTask || !window.confirm('确定取消此任务吗？已提交给生成服务的任务可能无法撤回。')) return
    setCancelBusy(true)
    setCancelError('')
    try { await onCancelTask() }
    catch { setCancelError('取消失败，任务可能已经进入不可取消阶段，请刷新状态后重试') }
    finally { setCancelBusy(false) }
  }
  const ready = task.variants.filter(variant => Boolean(variant.downloadArtifactId))
  const media = taskPlayableMedia(task, basket.relatedMultiSourceTasksForTask(task.id).concat(task.variants.flatMap(variant => basket.singleSourceVersionsFor(variant.id))))
  const primary = taskPrimaryMedia(media)
  const hasUnseenCut = hasUnseenCutflowOutput(basket.relatedMultiSourceTasksForTask(task.id), basket.multiTasksLoaded, Boolean(basket.multiTaskError))
  const state = taskCenterState(task,
    basket.relatedMultiSourceTasksForTask(task.id).concat(task.variants.flatMap(variant => basket.singleSourceVersionsFor(variant.id))),
    { loaded: !basket.enabled || basket.multiTasksLoaded, error: Boolean(basket.multiTaskError), now: basket.multiTasksCheckedAt },
  )
  // 查询剪辑状态时不要把选片入口隐藏；每条原片仍单独禁用剪辑中的来源。
  // 只有仍在生成原片的任务不开放选片，已完成和已剪过的原片都可再次选择。
  const needsAttention = taskAttentionGroup(task,
    basket.relatedMultiSourceTasksForTask(task.id).concat(task.variants.flatMap(variant => basket.singleSourceVersionsFor(variant.id))),
    { loaded: !basket.enabled || basket.multiTasksLoaded, error: Boolean(basket.multiTaskError), now: basket.multiTasksCheckedAt }) === 'attention'
  const statusLabel = task.phase === 'PARTIALLY_DELIVERABLE' ? '部分完成' : FAILED_PHASES.has(task.phase) ? '生成失败' : state.label
  const canManageAlert = needsAttention && canWrite && Boolean(onDismissAttention || onRestoreAttention || (task.actionCapabilities?.canCancel && onCancelTask))
  const canSelectClips = basket.enabled && canWrite && ready.length > 0
    && !ACTIVE_PHASES.has(task.phase) && task.phase !== 'CANCELLED'

  return <article className="task-center-card">
    <div className="task-center-card__main">
      <button type="button" className="task-center-card__cover"
        onClick={() => primary ? onQuickPlay(task, primary.key) : onOpen()}
        aria-label={primary ? '播放' + primary.label + '：' + task.title : '查看任务进度：' + task.title}>
        {primary ? <TaskMediaCover media={primary} cutStatus={taskCutCoverStatus(primary, state, task.variants.length)} /> : <span className="task-center-card__progress-cover">
          {ACTIVE_PHASES.has(task.phase) ? <LoaderCircle className="spin" size={20} aria-hidden="true" /> : <CircleAlert size={20} aria-hidden="true" />}
          <small>{statusLabel}</small>
        </span>}
      </button>
      <button type="button" className="task-center-card__info" onClick={onOpen} aria-label={'任务详情：' + task.title}>
        <strong className="task-center-card__title" title={task.title}>{task.title}</strong>
        <span className="task-center-card__sub">{task.clientTaskId || task.id.slice(0, 8)} · {formatTime(task.createdAt)}</span>
        <span className="task-center-card__statusline">
          <span className={'task-center-card__edit-state' + (acknowledged && needsAttention ? '' : state.group === 'active' ? ' is-active' : state.group === 'done' ? ' is-done' : state.group === 'attention' ? ' is-attention' : '')}>{statusLabel}</span>
          {hasUnseenCut ? <span className="task-center-card__unseen" aria-label="有未看的新成片">未看</span> : null}
          {acknowledged && needsAttention ? <span className="task-center-card__muted-indicator" title="仅已忽略提醒，任务状态未改变">已忽略</span> : null}
          <span className="task-center-card__count">{ready.length}/{task.variants.length} 原片</span>
        </span>
      </button>
      <div className="task-center-card__controls">
        {canSelectClips ? <TaskClipPickerLauncher task={task} basket={basket} /> : null}
        {canManageAlert ? <details className="task-center-card__more">
          <summary aria-label={'更多操作：' + task.title} title="更多操作"><MoreHorizontal size={18} /></summary>
          <div className="task-center-card__menu" role="group" aria-label="任务操作">
            {!acknowledged && task.actionCapabilities?.canCancel && onCancelTask
              ? <button type="button" disabled={cancelBusy} onClick={event => {
                event.currentTarget.closest('details')?.removeAttribute('open')
                void cancel()
              }}>{cancelBusy ? '取消中…' : '取消任务'}</button>
              : null}
            {acknowledged
              ? onRestoreAttention ? <button type="button" onClick={event => {
                event.currentTarget.closest('details')?.removeAttribute('open')
                onRestoreAttention()
              }}>恢复提醒</button> : null
              : onDismissAttention ? <button type="button" title="仅隐藏提醒，不影响任务和剪辑"
                  onClick={event => {
                    event.currentTarget.closest('details')?.removeAttribute('open')
                    onDismissAttention()
                  }}>忽略提醒</button> : null}
          </div>
        </details> : null}
      </div>
    </div>
    {cancelError ? <p role="alert" className="task-center-card__reason">{cancelError}</p> : null}
    {taskHasActionableReason(task) ? <p className="task-center-card__reason">{taskFailureMessage(task)}</p> : null}
  </article>
}

type RecordsViewProps = Omit<Parameters<typeof RecordsViewBody>[0], 'basket'> & { sharedBasket?: MultiCutBasketController }

export function RecordsView(props: RecordsViewProps) {
  if (props.sharedBasket) return <RecordsViewBody {...props} basket={props.sharedBasket} />
  return <RecordsViewLocal {...props} />
}

// Standalone support for test fixtures and non-application embedding.
function RecordsViewLocal(props: RecordsViewProps) {
  const { tasks, canWrite = true, multiCutReady = false, storageKey, onPlay } = props
  const basket = useMultiCutBasket({ tasks, canWrite, enabled: multiCutReady, storageKey })
  return <>
    <RecordsViewBody {...props} basket={basket} />
    {multiCutReady ? <MultiCutBasket basket={basket} onPlay={(taskId, variantId) => {
      const task = tasks.find(item => item.id === taskId)
      const variant = task?.variants.find(item => item.id === variantId)
      if (task && variant) onPlay(task, variant)
    }} /> : null}
  </>
}

function RecordsViewBody({ tasks, hasMore, loadingMore, lastSyncedAt, syncError = false, onLoadMore, onRefresh, onOpen, onCreateTask, canGenerate = true, initialFilter = 'all', initialQuery = '', onStateChange, canWrite = true, basket, attentionAcknowledgements = {}, onDismissAttention, onRestoreAttention, onCancelTask, attentionError = '' }: {
  tasks: Task[]
  hasMore: boolean
  loadingMore: boolean
  lastSyncedAt: number | null
  syncError?: boolean
  onLoadMore: () => void
  onRefresh?: () => Promise<void>
  onOpen: (task: Task) => void
  onPlay: (task: Task, variant: Variant) => void
  onRemix: (task: Task) => void
  onFeedbackChanged: (task: Task) => void
  onCreateTask: () => void
  onEditing?: (task: Task, variant: Variant) => void
  canReview?: boolean
  canGenerate?: boolean
  initialFilter?: RecordFilter
  initialQuery?: string
  onStateChange?: (filter: RecordFilter, query: string, replace?: boolean) => void
  canWrite?: boolean
  multiCutReady?: boolean
  storageKey?: string
  basket: MultiCutBasketController
  attentionAcknowledgements?: TaskAttentionMap
  onDismissAttention?: (task: Task, signature: string) => void
  onRestoreAttention?: (task: Task) => void
  onCancelTask?: (task: Task) => Promise<void>
  attentionError?: string
}) {
  const filter = initialFilter
  const query = initialQuery
  const [refreshing, setRefreshing] = useState(false)
  const [coolingDown, setCoolingDown] = useState(false)
  const [refreshFailed, setRefreshFailed] = useState(false)
  const refreshInFlight = useRef(false)
  const lastManualRefreshAt = useRef<number | null>(null)
  const refreshCooldownTimer = useRef<number | null>(null)
  useEffect(() => () => {
    if (refreshCooldownTimer.current !== null) window.clearTimeout(refreshCooldownTimer.current)
  }, [])
  const updateFilter = (next: RecordFilter) => onStateChange?.(next, query)
  const updateQuery = (next: string) => onStateChange?.(filter, next, true)
  const refreshCurrentStatus = async () => {
    const now = Date.now()
    if (!onRefresh || refreshInFlight.current ||
      (lastManualRefreshAt.current !== null && now - lastManualRefreshAt.current < MANUAL_REFRESH_COOLDOWN_MS)) return
    refreshInFlight.current = true
    lastManualRefreshAt.current = now
    setRefreshing(true)
    setCoolingDown(true)
    setRefreshFailed(false)
    if (refreshCooldownTimer.current !== null) window.clearTimeout(refreshCooldownTimer.current)
    refreshCooldownTimer.current = window.setTimeout(() => {
      refreshCooldownTimer.current = null
      setCoolingDown(false)
    }, MANUAL_REFRESH_COOLDOWN_MS)
    try {
      if (basket.enabled) basket.refreshMultiTasks()
      await onRefresh()
    } catch {
      setRefreshFailed(true)
    } finally {
      refreshInFlight.current = false
      setRefreshing(false)
    }
  }
  const batchesOf = (task: Task) => basket.relatedMultiSourceTasksForTask(task.id)
    .concat(task.variants.flatMap(variant => basket.singleSourceVersionsFor(variant.id)))
  const cutStatus = { loaded: !basket.enabled || basket.multiTasksLoaded, error: Boolean(basket.multiTaskError), now: basket.multiTasksCheckedAt }
  const signatureOf = (task: Task) => taskAttentionSignature(task, batchesOf(task))
  const isAcknowledged = (task: Task) => isTaskAttentionAcknowledged(task.id, signatureOf(task), attentionAcknowledgements)
  const [dateRange, setDateRange] = useState<TaskDateRange | null>(() => typeof window === 'undefined' ? null : parseTaskDateRange(window.location.search))
  const [sortAsc, setSortAsc] = useState(false)
  const [viewMode, setViewMode] = useState<'list' | 'grid'>(() => {
    try { return window.sessionStorage.getItem('allwe-task-center-view') === 'list' ? 'list' : 'grid' } catch { return 'grid' }
  })
  useEffect(() => { try { window.sessionStorage.setItem('allwe-task-center-view', viewMode) } catch { /* private mode */ } }, [viewMode])
  const [quickPreview, setQuickPreview] = useState<{ taskId: string; key: string } | null>(null)
  const quickTask = quickPreview ? tasks.find(task => task.id === quickPreview.taskId) ?? null : null
  const automaticPages = useRef(0)
  const pagingPending = useRef(false)
  useEffect(() => {
    const restore = () => setDateRange(parseTaskDateRange(window.location.search))
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [])
  const onDateChange = (next: TaskDateRange | null) => {
    setDateRange(next)
    const url = new URL(window.location.href)
    if (next) { url.searchParams.set('from', next.start); url.searchParams.set('to', next.end) }
    else { url.searchParams.delete('from'); url.searchParams.delete('to') }
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
  }
  const groupOf = (task: Task) => {
    const group = taskAttentionGroup(task, batchesOf(task), cutStatus)
    return group === 'attention' && isAcknowledged(task) ? 'ignored' : group
  }
  const groups: Array<{ value: RecordFilter; label: string; test: (task: Task) => boolean }> = [
    { value: 'all', label: '全部任务', test: () => true },
    { value: 'attention', label: '待我处理', test: task => groupOf(task) === 'attention' },
    { value: 'active', label: '进行中', test: task => groupOf(task) === 'active' },
    { value: 'done', label: '已完成', test: task => groupOf(task) === 'done' },
  ]
  const effectiveFilter = filter === 'partial' ? 'done' : filter === 'failed' ? 'attention' : filter === 'cancelled' ? 'all' : filter
  const keyword = query.trim().toLowerCase()
  const extraLabel = (task: Task) => task.phase === 'CANCELLED' ? '已取消 取消' : task.phase === 'PARTIALLY_DELIVERABLE' ? '部分完成' : FAILED_PHASES.has(task.phase) ? '生成失败 失败' : ''
  const matchesQuery = (task: Task) => !keyword || [task.title, task.id, task.clientTaskId, task.siteProfileId, extraLabel(task)].some(value => value?.toLowerCase().includes(keyword))
  const matchesBase = (task: Task) => matchesQuery(task) && taskCreatedOn(task, dateRange)
  const visible = tasks.filter(task => (groups.find(item => item.value === effectiveFilter) ?? groups[0]).test(task) && matchesBase(task))
    .sort((a, b) => (Date.parse(a.createdAt) - Date.parse(b.createdAt)) * (sortAsc ? 1 : -1))
  const countOf = (test: (task: Task) => boolean) => tasks.filter(task => test(task) && matchesBase(task)).length
  const dateCounts = new Map<string, number>()
  for (const task of visible) {
    const date = localDayKey(new Date(task.createdAt))
    dateCounts.set(date, (dateCounts.get(date) ?? 0) + 1)
  }
  // UpdatedAt is always >= CreatedAt: once the oldest loaded updated time
  // predates the selected start, no unscanned page can contain a matching creation date.
  const oldestUpdate = tasks.reduce((min, task) => Math.min(min, Date.parse(task.updatedAt) || Number.POSITIVE_INFINITY), Number.POSITIVE_INFINITY)
  const dateCoverageComplete = !dateRange || oldestUpdate < new Date(dateRange.start + 'T00:00:00').getTime() || !hasMore
  const lookingForHistory = Boolean(dateRange && hasMore && !dateCoverageComplete)
  useEffect(() => { automaticPages.current = 0; pagingPending.current = false }, [dateRange?.start, dateRange?.end])
  useEffect(() => { if (loadingMore) pagingPending.current = true; else if (pagingPending.current) pagingPending.current = false }, [loadingMore])
  useEffect(() => {
    if (!lookingForHistory || loadingMore || pagingPending.current || automaticPages.current >= 8) return
    pagingPending.current = true
    automaticPages.current += 1
    onLoadMore()
  }, [lookingForHistory, loadingMore, oldestUpdate, onLoadMore])
  return <div className="page-view task-center-view">
    <div className="page-title"><div><h1>任务中心</h1></div>
      <div className="records-page-actions">
        <DataFreshness timestamp={lastSyncedAt} stale={syncError} />
        <label className="search-field"><Search size={17} /><input value={query} onChange={event => updateQuery(event.target.value)} placeholder="搜索任务名称、编号或品牌" aria-label="搜索任务名称、编号或品牌" />{keyword ? <button type="button" className="search-field-clear" onClick={() => updateQuery('')} aria-label="清除搜索关键词"><X size={15} /></button> : null}</label>
        <button type="button" className="icon-button" onClick={() => void refreshCurrentStatus()} disabled={refreshing || coolingDown || !onRefresh} aria-label={refreshing ? '正在刷新任务状态' : coolingDown ? '刷新冷却中，请稍后重试' : '刷新任务状态'} title={refreshing ? '正在刷新任务状态' : coolingDown ? '5 秒后可再次刷新' : '刷新任务状态'}>{refreshing ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}</button>
        {refreshFailed ? <span className="task-center-refresh-error" role="alert">刷新失败，请重试</span> : null}
        {attentionError ? <span className="task-center-refresh-error" role="alert">{attentionError}</span> : null}
      </div>
    </div>
    <nav className="task-center-filters" aria-label="按任务状态筛选">
      <div className="harmony-filter-segment" role="group" aria-label="任务状态"><span className="harmony-segment-thumb" aria-hidden="true"/>
      {groups.map(item => <button key={item.value} type="button" className={effectiveFilter === item.value ? 'is-active' : ''} aria-pressed={effectiveFilter === item.value} onClick={() => updateFilter(item.value)}>
        {item.label}<span>{countOf(item.test)}</span>
      </button>)}
      </div>
      <TaskDatePicker value={dateRange} onChange={onDateChange} />
    </nav>

    {keyword ? <p className="task-center-search-note" role="status">{hasMore ? '仅搜索已加载记录' : '搜索全部已加载记录'}：「{query.trim()}」，当前筛选匹配 {visible.length} 条任务</p> : null}
    <div className="task-center-resultbar"><span><strong>{visible.length}</strong> 条任务{dateRange ? ' · ' + taskDateLabel(dateRange) : ''}{lookingForHistory ? ' · 正在补齐历史记录' : ''}</span><button type="button" onClick={() => setSortAsc(old => !old)}>创建时间：{sortAsc ? '最早在前 ↑' : '最新在前 ↓'}</button>

      <div className="task-center-view-switch" aria-label="任务展示方式"><span className="harmony-segment-thumb" aria-hidden="true"/><button type="button" aria-label="紧凑列表" aria-pressed={viewMode === 'list'} onClick={() => setViewMode('list')}><List size={16} /></button><button type="button" aria-label="视频卡片" aria-pressed={viewMode === 'grid'} onClick={() => setViewMode('grid')}><LayoutGrid size={16} /></button></div>
    </div>
    {visible.length ? <div className={'task-center-list' + (viewMode === 'grid' ? ' is-grid' : '')} aria-label="任务列表" data-view={viewMode} data-filter={effectiveFilter}>
      {visible.map((task, index) => {
        const day = localDayKey(new Date(task.createdAt))
        const priorDay = index > 0 ? localDayKey(new Date(visible[index - 1].createdAt)) : null
        return <Fragment key={task.id}>
          {day !== priorDay ? <div className="task-center-day-group"><span>{day === localDayKey(new Date()) ? '今天' : day} · {day.slice(5).replace('-', '/')}</span><small>{dateCounts.get(day)} 条任务</small></div> : null}
          <TaskCenterCard task={task} basket={basket} canWrite={canWrite}
            acknowledged={isAcknowledged(task)}
            onDismissAttention={onDismissAttention ? () => onDismissAttention(task, signatureOf(task)) : undefined}
            onRestoreAttention={onRestoreAttention ? () => onRestoreAttention(task) : undefined}
            onCancelTask={onCancelTask ? () => onCancelTask(task) : undefined}
            onOpen={() => onOpen(task)} onQuickPlay={(item, key) => setQuickPreview({ taskId: item.id, key })} />
        </Fragment>
      })}
    </div> : <EmptyState icon={<ListVideo size={24} />} title={keyword ? hasMore ? '已加载记录中没有匹配任务' : '没有匹配的任务' : '当前分类暂无任务'} description={keyword ? hasMore ? '还有历史记录未加载，可继续加载并保留当前搜索条件。' : '尝试换个关键词或切换任务分类。' : hasMore ? '还有历史记录未加载，可继续加载查看当前分类。' : '在视频制作中创建任务，完成后会出现在这里。'} action={keyword ? <button type="button" className="secondary-button" onClick={() => updateQuery('')}>清除搜索</button> : canGenerate ? <button type="button" className="primary-button" onClick={onCreateTask}>去创建任务</button> : undefined} />}
    {quickPreview && quickTask ? <TaskQuickPreview key={quickTask.id} media={taskPlayableMedia(quickTask, batchesOf(quickTask))} startKey={quickPreview.key} onClose={() => setQuickPreview(null)} onViewed={() => basket.refreshMultiTasks()} /> : null}
    {hasMore ? <div className="records-load-more"><button type="button" className="secondary-button" disabled={loadingMore} onClick={onLoadMore}>{loadingMore ? <LoaderCircle className="spin" size={16} /> : null}{loadingMore ? '正在加载' : keyword ? '继续加载历史并查找' : '加载更早记录'}</button></div> : null}
    <details className="task-center-history"><summary>剪辑任务与成片历史 <ChevronDown size={15} /></summary><CutflowHistory tasks={basket.multiTasks} loaded={basket.multiTasksLoaded} error={basket.multiTaskError} onRefresh={basket.refreshMultiTasks} /></details>
  </div>
}
