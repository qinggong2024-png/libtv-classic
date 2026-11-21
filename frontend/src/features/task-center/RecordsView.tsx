import { ChevronDown, ChevronRight, ListVideo, LoaderCircle, Play, RefreshCw, Search, X } from 'lucide-react'
import { Fragment, useEffect, useRef, useState } from 'react'
import type { RecordsFilter as AppRecordsFilter } from '../../app/appTypes'
import type { Task, Variant } from '../../types'
import { ACTIVE_PHASES, FAILED_PHASES } from '../../domain/workflowStates'
import { taskCenterState } from '../../domain/taskCenterState'
import { hasUnseenCutflowOutput } from '../../domain/cutflowUnread'
import { DataFreshness } from '../../shared/components/DataFreshness'
import { EmptyState } from '../../shared/components/Section'
import { MultiCutBasket } from '../production/MultiCutBasket'
import { CutflowHistory } from '../production/CutflowHistory'
import { useMultiCutBasket, type MultiCutBasketController } from '../production/useMultiCutBasket'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'
import { formatTime, taskFailureMessage, variantDisplayName } from '../../domain/taskPresentation'
import { TaskThumb } from '../../shared/components/Media'
import { TaskDatePicker } from './TaskDatePicker'
import { localDayKey, parseTaskDateRange, taskCreatedOn, taskDateLabel, type TaskDateRange } from './TaskDateRange'
import './taskCenterCards.css'
import './TaskDatePicker.css'

export type RecordFilter = AppRecordsFilter

const MANUAL_REFRESH_COOLDOWN_MS = 5000

function TaskCenterCard({ task, basket, canWrite, onOpen, onPlay }: {
  task: Task
  basket: MultiCutBasketController
  canWrite: boolean
  onOpen: () => void
  onPlay: (variant: Variant) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const ready = task.variants.filter(variant => Boolean(variant.downloadArtifactId))
  const hasUnseenCut = hasUnseenCutflowOutput(basket.relatedMultiSourceTasksForTask(task.id), basket.multiTasksLoaded, Boolean(basket.multiTaskError))
  const state = taskCenterState(task,
    basket.relatedMultiSourceTasksForTask(task.id).concat(task.variants.flatMap(variant => basket.singleSourceVersionsFor(variant.id))),
    { loaded: !basket.enabled || basket.multiTasksLoaded, error: Boolean(basket.multiTaskError), now: basket.multiTasksCheckedAt },
  )
  // 查询剪辑状态时不要把选片入口隐藏；每条原片仍单独禁用剪辑中的来源。
  // 只有仍在生成原片的任务不开放选片，已完成和已剪过的原片都可再次选择。
  const canSelectClips = basket.enabled && canWrite && ready.length > 0
    && !ACTIVE_PHASES.has(task.phase) && task.phase !== 'CANCELLED'

  return <article className="task-center-card">
    <button type="button" className="task-center-card__main" onClick={onOpen} aria-label={'查看任务详情：' + task.title}>
      <TaskThumb task={task} />
      <span className="task-center-card__identity">
        <strong>{task.title}</strong>
        <small>{formatTime(task.createdAt)} · {task.assetCount} 张素材 · {task.variants.length} 个结果</small>
        <span className="task-center-card__statuses">
          <span className={'task-center-card__edit-state' + (state.group === 'active' ? ' is-active' : state.group === 'done' ? ' is-done' : state.group === 'attention' ? ' is-attention' : '')}>{state.label}</span>
          {task.phase === 'PARTIALLY_DELIVERABLE' ? <span className="task-center-card__edit-state is-partial">部分完成</span> : null}
          {FAILED_PHASES.has(task.phase) ? <span className="task-center-card__edit-state is-partial">生成失败</span> : null}
          {hasUnseenCut ? <span className="task-center-card__unseen" aria-label="有未看的新成片">未看</span> : null}
        </span>
      </span>
      <span className="task-center-card__progress">
        <strong>{state.group === 'done' ? `成片 ${state.outputs} 条` : `原片 ${ready.length}/${task.variants.length}`}</strong>
        <small>{state.group === 'done' ? '可查看和下载成片' : state.detail}</small>
      </span>
      <span className="task-center-card__next">{state.group === 'done' ? '查看成片' : state.group === 'attention' ? '去处理' : '查看进度'} <ChevronRight size={17} aria-hidden="true" /></span>
    </button>
    {task.phaseReasonCode ? <p className="task-center-card__reason">{taskFailureMessage(task)}</p> : null}
    {canSelectClips ? <div className="task-center-card__bottom">
      <span>{state.manualSelection ? '本任务已关闭自动送剪，可手动选择原片' : '可跨任务合并原片，也可创建新的剪辑版本'}</span>
      <button type="button" className="text-button" aria-expanded={expanded} onClick={() => setExpanded(open => !open)}>
        {expanded ? '收起原片' : state.group === 'done' ? '重新选择原片' : '选择原片送剪'} <ChevronDown size={15} className={expanded ? 'is-open' : ''} />
      </button>
    </div> : null}
    {expanded && canSelectClips ? <div className="task-center-card__clips" aria-label={task.title + '可选择的原片'}>
      {ready.map(variant => {
        const inProgress = basket.isInProgress(variant.id)
        const selected = basket.isSelected(variant.id)
        return <div className="task-center-card__clip" key={variant.id}>
          <button type="button" className="task-center-card__clip-preview" onClick={() => onPlay(variant)} aria-label={'播放' + variantDisplayName(variant)}>
            <VideoThumbnail artifactId={variant.downloadArtifactId!} alt={variantDisplayName(variant) + '视频封面'} />
            <Play size={16} aria-hidden="true" />
          </button>
          <span>{variantDisplayName(variant)}</span>
          <button type="button" className="compact-button" disabled={inProgress || (!selected && basket.atCapacity)} onClick={() => basket.toggle(variant.id)} aria-pressed={selected}>
            {inProgress ? '剪辑中' : selected ? '已选择 ✓' : basket.isPreviouslyCut(variant.id) ? '重新剪辑' : '选择送剪'}
          </button>
        </div>
      })}
    </div> : null}
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

function RecordsViewBody({ tasks, hasMore, loadingMore, lastSyncedAt, syncError = false, onLoadMore, onRefresh, onOpen, onPlay, onCreateTask, canGenerate = true, initialFilter = 'all', initialQuery = '', onStateChange, canWrite = true, basket }: {
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
  const stateOf = (task: Task) => taskCenterState(task,
    basket.relatedMultiSourceTasksForTask(task.id).concat(task.variants.flatMap(variant => basket.singleSourceVersionsFor(variant.id))),
    { loaded: !basket.enabled || basket.multiTasksLoaded, error: Boolean(basket.multiTaskError), now: basket.multiTasksCheckedAt },
  )
  const [dateRange, setDateRange] = useState<TaskDateRange | null>(() => typeof window === 'undefined' ? null : parseTaskDateRange(window.location.search))
  const [sortAsc, setSortAsc] = useState(false)
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
    if (task.phase === 'CANCELLED') return 'cancelled'
    if (FAILED_PHASES.has(task.phase)) return 'attention'
    if (task.phase === 'PARTIALLY_DELIVERABLE') return 'done'
    return stateOf(task).group
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
    <div className="page-title"><div><h1>任务中心</h1><p>生成、剪辑、成片进度都在这里查看。</p></div>
      <div className="records-page-actions">
        <DataFreshness timestamp={lastSyncedAt} stale={syncError} />
        <label className="search-field"><Search size={17} /><input value={query} onChange={event => updateQuery(event.target.value)} placeholder="搜索任务名称、编号或品牌" aria-label="搜索任务名称、编号或品牌" />{keyword ? <button type="button" className="search-field-clear" onClick={() => updateQuery('')} aria-label="清除搜索关键词"><X size={15} /></button> : null}</label>
        <button type="button" className="icon-button" onClick={() => void refreshCurrentStatus()} disabled={refreshing || coolingDown || !onRefresh} aria-label={refreshing ? '正在刷新任务状态' : coolingDown ? '刷新冷却中，请稍后重试' : '刷新任务状态'} title={refreshing ? '正在刷新任务状态' : coolingDown ? '5 秒后可再次刷新' : '刷新任务状态'}>{refreshing ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}</button>
        {refreshFailed ? <span className="task-center-refresh-error" role="alert">刷新失败，请重试</span> : null}
      </div>
    </div>
    <nav className="task-center-filters" aria-label="按任务状态筛选">
      {groups.map(item => <button key={item.value} type="button" className={effectiveFilter === item.value ? 'is-active' : ''} aria-pressed={effectiveFilter === item.value} onClick={() => updateFilter(item.value)}>
        {item.label}<span>{countOf(item.test)}</span>
      </button>)}
      <TaskDatePicker value={dateRange} onChange={onDateChange} />
    </nav>
    {filter === 'attention' ? <p className="task-center-search-note">待我处理包含生成待确认、状态异常、待送剪与剪辑异常；侧栏数字仅统计需处理的生成任务。</p> : null}
    {keyword ? <p className="task-center-search-note" role="status">{hasMore ? '仅搜索已加载记录' : '搜索全部已加载记录'}：「{query.trim()}」，当前筛选匹配 {visible.length} 条任务</p> : null}
    <div className="task-center-resultbar"><span><strong>{visible.length}</strong> 条任务{dateRange ? ' · ' + taskDateLabel(dateRange) : ''}{lookingForHistory ? ' · 正在补齐历史记录' : ''}</span><button type="button" onClick={() => setSortAsc(old => !old)}>创建时间：{sortAsc ? '最早在前 ↑' : '最新在前 ↓'}</button></div>
    {visible.length ? <div className="task-center-list" aria-label="任务列表">
      {visible.map((task, index) => {
        const day = localDayKey(new Date(task.createdAt))
        const priorDay = index > 0 ? localDayKey(new Date(visible[index - 1].createdAt)) : null
        return <Fragment key={task.id}>
          {day !== priorDay ? <div className="task-center-day-group"><span>{day === localDayKey(new Date()) ? '今天' : day} · {day.slice(5).replace('-', '/')}</span><small>{dateCounts.get(day)} 条任务</small></div> : null}
          <TaskCenterCard task={task} basket={basket} canWrite={canWrite} onOpen={() => onOpen(task)} onPlay={variant => onPlay(task, variant)} />
        </Fragment>
      })}
    </div> : <EmptyState icon={<ListVideo size={24} />} title={keyword ? hasMore ? '已加载记录中没有匹配任务' : '没有匹配的任务' : '当前分类暂无任务'} description={keyword ? hasMore ? '还有历史记录未加载，可继续加载并保留当前搜索条件。' : '尝试换个关键词或切换任务分类。' : hasMore ? '还有历史记录未加载，可继续加载查看当前分类。' : '在视频制作中创建任务，完成后会出现在这里。'} action={keyword ? <button type="button" className="secondary-button" onClick={() => updateQuery('')}>清除搜索</button> : canGenerate ? <button type="button" className="primary-button" onClick={onCreateTask}>去创建任务</button> : undefined} />}
    {hasMore ? <div className="records-load-more"><button type="button" className="secondary-button" disabled={loadingMore} onClick={onLoadMore}>{loadingMore ? <LoaderCircle className="spin" size={16} /> : null}{loadingMore ? '正在加载' : keyword ? '继续加载历史并查找' : '加载更早记录'}</button></div> : null}
    <details className="task-center-history"><summary>剪辑任务与成片历史 <ChevronDown size={15} /></summary><CutflowHistory tasks={basket.multiTasks} loaded={basket.multiTasksLoaded} error={basket.multiTaskError} onRefresh={basket.refreshMultiTasks} /></details>
  </div>
}
