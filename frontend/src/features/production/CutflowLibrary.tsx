import { ChevronDown, ChevronUp, Film, LoaderCircle, Play, RefreshCw, Scissors, X, ArrowUp, ArrowDown } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { createMultiCutflowTask, listCutflowHandoffs, listMultiCutflowTasks, retryMultiCutflowTask } from '../../api'
import type { MultiCutflowTask } from '../../api'
import type { Task, Variant } from '../../types'
import { cutflowPresentation, multiCutflowPresentation } from '../../domain/cutflowPresentation'
import { formatTime, variantDisplayName } from '../../domain/taskPresentation'
import { EmptyState } from '../../shared/components/Section'
import { VideoThumbnail } from '../../shared/components/VideoThumbnail'
import { RemoteVideoPreview } from '../../shared/components/Media'
import { CutflowControl } from './CutflowControl'
import { cutflowSources, cutflowSourceSummary, cutflowBrandConflict, cutflowUnverifiedBrandCount, cutflowSourceCanJoinBasket, moveCutflowSource, toggleCutflowSelection, type CutflowLibraryFilter, type CutflowSourceState } from './cutflowLibraryModel'
import './cutflow.css'

const filters: Array<{ value: CutflowLibraryFilter; label: string; empty: string; hint: string }> = [
  { value: 'cutflow-ready', label: '待送剪辑', empty: '没有待送剪辑的原片', hint: '生成完成的原片会出现在这里。选择一条原片，展开后送入剪辑。' },
  { value: 'cutflow-editing', label: '剪辑任务', empty: '没有进行中的剪辑任务', hint: '送入剪辑后，在这里查看传输、剪辑进度，以及需要处理的问题。' },
  { value: 'cutflow-finished', label: '剪辑成片', empty: '还没有回传的剪辑成片', hint: '成片回传后集中显示在这里，可预览、下载，并查看对应的生成原片。' },
]

const createAttemptKey = () => (
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
)

export function CutflowLibrary({ tasks, query, filter, selectedSourceId: selected, onSelect, onFilterChange, onOpen, onPlay, canWrite, multiCutEnabled }: {
  tasks: Task[]; query: string; filter: CutflowLibraryFilter; selectedSourceId: string | null
  onSelect: (sourceId: string | null) => void; onFilterChange: (value: CutflowLibraryFilter, sourceId?: string | null) => void
  onOpen: (task: Task) => void; onPlay: (task: Task, variant: Variant) => void; canWrite: boolean
  multiCutEnabled: boolean
}) {
  const sources = useMemo(() => cutflowSources(tasks), [tasks])
  const sourceKey = JSON.stringify(sources.map(source => source.variant.id))
  const [states, setStates] = useState<Record<string, CutflowSourceState>>({})
  const [multiTasks, setMultiTasks] = useState<MultiCutflowTask[]>([])
  const [multiTaskError, setMultiTaskError] = useState('')
  const [reading, setReading] = useState(true)
  const [refreshToken, setRefreshToken] = useState(0)
  const [basket, setBasket] = useState<string[]>(() => {
    try {
      const saved = sessionStorage.getItem('cutflow-multi-source-selection')
      const parsed: unknown = saved ? JSON.parse(saved) : []
      return Array.isArray(parsed) && parsed.every(id => typeof id === 'string') ? parsed : []
    } catch { return [] }
  })
  const [basketOpen, setBasketOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [retryingMultiId, setRetryingMultiId] = useState<string | null>(null)
  const [cutPreview, setCutPreview] = useState<{ mediaUrl: string; title: string } | null>(null)
  const [basketMessage, setBasketMessage] = useState('')
  const [policyConfirmed, setPolicyConfirmed] = useState(false)
  useEffect(() => {
    try { sessionStorage.setItem('cutflow-multi-source-selection', JSON.stringify(basket)) } catch { /* session storage may be unavailable */ }
  }, [basket])
  useEffect(() => {
    const ids = JSON.parse(sourceKey) as string[]
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      // Bound concurrency for accounts with many loaded tasks. Each read retains
      // the existing per-variant ownership contract and complete source history.
      let nextIndex = 0
      let active = false
      const multiRead = listMultiCutflowTasks(controller.signal).then(result => {
        if (controller.signal.aborted) return
        setMultiTasks(result.tasks)
        setMultiTaskError('')
        active ||= result.tasks.some(task => multiCutflowPresentation(task).active)
      }).catch(cause => {
        if (!controller.signal.aborted) {
          setMultiTaskError(cause instanceof Error ? cause.message : '多源剪辑状态读取失败')
        }
      })
      await Promise.all([
        multiRead,
        ...Array.from({ length: Math.min(ids.length, 6) }, async () => {
        while (nextIndex < ids.length && !controller.signal.aborted) {
          const id = ids[nextIndex++]
          try {
            const result = await listCutflowHandoffs(id, controller.signal)
            if (controller.signal.aborted) return
            active ||= result.handoffs.some(item => cutflowPresentation(item).active)
            setStates(previous => ({ ...previous, [id]: result }))
          } catch (cause) {
            if (!controller.signal.aborted) setStates(previous => ({ ...previous, [id]: {
              enabled: previous[id]?.enabled ?? true, handoffs: previous[id]?.handoffs ?? [],
              error: cause instanceof Error ? cause.message : '剪辑状态读取失败',
            } }))
          }
        }
        }),
      ])
      if (!controller.signal.aborted) {
        setReading(false)
        timer = setTimeout(() => void refresh(), active ? 15000 : 30000)
      }
    }
    void refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [sourceKey, refreshToken])

  const keyword = query.trim().toLowerCase()
  const matched = sources.filter(source => source.task.title.toLowerCase().includes(keyword))
  const entries = matched.map(source => ({ ...source, state: states[source.variant.id], ...cutflowSourceSummary(source, states[source.variant.id]) }))
  const multiSourceVariantIds = new Set(multiTasks
    .filter(task => multiCutflowPresentation(task).active)
    .flatMap(task => task.sources.map(source => source.variantId)))
  const validIds = new Set(sources.filter(source => (
    cutflowSourceCanJoinBasket(states[source.variant.id], source.artifactId)
    && !multiSourceVariantIds.has(source.variant.id)
  )).map(source => source.variant.id))
  // Keep the ordered IDs through refreshes; missing sources stay visible as
  // removable errors instead of disappearing from the user's selection.
  const visibleBasket = basket
  const basketEntries = visibleBasket.map(id => sources.find(source => source.variant.id === id)!).filter(Boolean)
  const brandConflict = cutflowBrandConflict(basketEntries)
  const unverifiedBrands = cutflowUnverifiedBrandCount(basketEntries)
  // Keep the expanded source in place when polling moves it to another stage;
  // the user may be watching or downloading its returned media.
  const visible = entries.filter(entry => entry.filter === filter || entry.variant.id === selected || (entry.state?.error && !entry.filter))
  const visibleMultiTasks = multiTasks.filter(task => (
    filter === 'cutflow-finished' ? task.outputs.length > 0 :
      filter === 'cutflow-editing' ? task.outputs.length === 0 : false
  ) && task.sources.some(source => source.title.toLowerCase().includes(keyword)))
  const tab = filters.find(item => item.value === filter)!
  const reload = () => { setReading(true); setRefreshToken(value => value + 1) }
  const allDisabled = sources.length > 0 && sources.every(source => states[source.variant.id]?.enabled === false)
  const pendingReads = entries.some(entry => !entry.state)
  const submitHint = !canWrite ? '当前账号没有送剪权限'
    : multiTaskError ? '多源剪辑状态读取失败，请刷新后再试'
      : brandConflict ? '所选原片分属不同品牌'
        : !basket.length ? '先选择至少一条原片'
          : basketEntries.length !== basket.length ? '部分原片已不可送剪，请先移除'
            : basketEntries.some(item => !validIds.has(item.variant.id)) ? '有原片正在剪辑或已有成片，请移除后再送剪'
              : !policyConfirmed ? '请先确认将按当前剪辑策略处理'
                : undefined
  const canSubmit = multiCutEnabled && canWrite && !submitting && !brandConflict
    && !multiTaskError
    && policyConfirmed && basket.length >= 1
    && basket.every(id => validIds.has(id))
    && basketEntries.length === basket.length
  const submitBasket = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setBasketMessage('')
    try {
      // One key per attempt: the button is disabled while in flight, so a retry
      // after a failure gets a fresh key and cannot resurrect a mixed basket.
      const submittedCount = basket.length
      await createMultiCutflowTask(basket, `cutflow-multi-${createAttemptKey()}`, true, true)
      setBasket([])
      setPolicyConfirmed(false)
      setBasketMessage(submittedCount === 1 ? '已送交 1 条原片剪辑，可在「剪辑任务」中查看进度。' : `已送交 ${submittedCount} 条原片剪辑，可在「剪辑任务」中查看进度。`)
      setRefreshToken(value => value + 1)
    } catch (cause) {
      setBasketMessage(cause instanceof Error && cause.message ? cause.message : '送剪失败，请稍后重试。')
    } finally {
      setSubmitting(false)
    }
  }
  const retryMultiTask = async (taskId: string) => {
    setRetryingMultiId(taskId)
    setBasketMessage('')
    try {
      await retryMultiCutflowTask(taskId)
      setBasketMessage('已恢复原剪辑任务，正在使用同一批次查询状态。')
      setRefreshToken(value => value + 1)
    } catch (cause) {
      setBasketMessage(cause instanceof Error && cause.message ? cause.message : '恢复原剪辑任务失败，请稍后重试。')
    } finally {
      setRetryingMultiId(null)
    }
  }
  return <section className="cutflow-library" aria-label="剪辑与成片">
    <div className="cutflow-library__toolbar">
      <div className="filter-tabs" aria-label="剪辑阶段">{filters.map(item => <button key={item.value} type="button" className={filter === item.value ? 'is-active' : ''} aria-pressed={filter === item.value} onClick={() => onFilterChange(item.value)}>{item.label}<span>{entries.filter(entry => entry.filter === item.value).length + (item.value === 'cutflow-finished' ? multiTasks.filter(task => task.outputs.length > 0).length : item.value === 'cutflow-editing' ? multiTasks.filter(task => task.outputs.length === 0).length : 0)}</span></button>)}</div>
      <button className="compact-button" type="button" disabled={reading} onClick={reload}><RefreshCw size={16} className={reading ? 'spin' : undefined} />刷新剪辑列表</button>
    </div>
    <p className="cutflow-library__hint">{tab.hint}</p>
    {entries.some(entry => entry.state?.error) ? <p className="cutflow-library__warning" role="alert">部分剪辑状态读取失败，保留上次结果。请刷新后核对，暂勿重复送剪辑。</p> : null}
    {multiTaskError ? <p className="cutflow-library__warning" role="alert">多源剪辑状态读取失败，保留上次结果。请刷新后核对，暂勿重复送剪辑。</p> : null}
    {pendingReads ? <p role="status"><LoaderCircle size={16} className="spin" />正在读取剪辑列表…</p> : null}
    {visibleMultiTasks.length ? <div className="cutflow-library__multi-list" aria-label="剪辑任务">{visibleMultiTasks.map(task => <article className="cutflow-library__multi-item" key={task.id}>
      <div className="cutflow-library__multi-heading"><strong>{task.outputs.length ? '剪辑成片' : '剪辑任务'}</strong><span className={`cutflow-library__status${task.outputs.length ? ' is-finished' : ''}`} role="status">{multiCutflowPresentation(task).label}</span></div>
      <ol className="cutflow-library__multi-sources">{task.sources.map(source => <li key={source.clipId}>{source.order}. {source.title}</li>)}</ol>
      {task.errorMessage ? <p className="cutflow-library__warning" role="alert">{task.errorMessage}</p> : null}
      {task.status === 'FAILED' && task.errorCode !== 'CUTFLOW_HISTORICAL_TASK_ISOLATED' ? <button className="compact-button" type="button" disabled={retryingMultiId === task.id} onClick={() => { void retryMultiTask(task.id) }}>{retryingMultiId === task.id ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{retryingMultiId === task.id ? '正在恢复' : '重试原任务'}</button> : null}
      {task.outputs.map(output => <div className="cutflow-library__multi-output" key={output.id}>
        <button type="button" className="cutflow-library__multi-preview" aria-label="预览剪辑成片" onClick={() => setCutPreview({ mediaUrl: output.mediaUrl, title: `剪辑成片 · ${task.sources.length} 条原片` })}>
          <video muted playsInline preload="metadata" src={output.mediaUrl} />
          <span><Play size={18} />预览成片</span>
        </button>
        <div><span>{output.durationSeconds.toFixed(1)} 秒 · {output.width}×{output.height} · {output.reviewStatus === 'PENDING' ? '待审核' : output.reviewStatus}</span><a href={`${output.mediaUrl}?download=true`} download>下载成片</a></div>
      </div>)}
    </article>)}</div> : null}
    <div className="cutflow-library__list">{visible.map(entry => {
      const expanded = selected === entry.variant.id
      const label = entry.state?.error ? '状态待核对' : entry.outputs.length ? `${entry.outputs.length} 条剪辑成片` : entry.current ? cutflowPresentation(entry.current).label : '待送剪辑'
      return <article className="cutflow-library__item" key={entry.variant.id}>
        <div className="cutflow-library__row">
          <button type="button" className="cutflow-library__source" aria-label={`播放生成原片：${entry.task.title} · ${variantDisplayName(entry.variant)}`} onClick={() => onPlay(entry.task, entry.variant)}><VideoThumbnail artifactId={entry.artifactId} alt={`${entry.task.title} 生成原片`} /><Play size={16} /></button>
          <div className="cutflow-library__identity"><strong>{entry.task.title}</strong><span>来源原片 · {variantDisplayName(entry.variant)}</span><small>{formatTime(entry.task.updatedAt)}</small></div>
          <span className={`cutflow-library__status${entry.outputs.length ? ' is-finished' : ''}`} role="status">{label}</span>
          <div className="cutflow-library__actions"><button className="text-button" type="button" onClick={() => onOpen(entry.task)}>来源任务</button>{entry.filter === 'cutflow-ready' && canWrite ? <button type="button" className={`compact-button${basket.includes(entry.variant.id) ? ' is-selected' : ''}`} aria-pressed={basket.includes(entry.variant.id)} disabled={!entry.state?.enabled} onClick={() => setBasket(previous => toggleCutflowSelection(previous, entry.variant.id))}>{basket.includes(entry.variant.id) ? '已加入剪辑篮' : '加入剪辑篮'}</button> : null}<button className="compact-button" type="button" aria-expanded={expanded} aria-controls={`editing-${entry.variant.id}`} onClick={() => onSelect(expanded ? null : entry.variant.id)}>{expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}{entry.outputs.length ? '预览 / 下载成片' : entry.current ? '查看剪辑任务' : '展开送剪辑'}</button></div>
        </div>
        {expanded ? <div id={`editing-${entry.variant.id}`} className="cutflow-library__detail"><CutflowControl taskId={entry.task.id} variantId={entry.variant.id} artifactId={entry.artifactId} siteProfileId={entry.task.siteProfileId} canWrite={canWrite} sourceLabel={`${entry.task.title} · ${variantDisplayName(entry.variant)}`} onStateRead={state => setStates(previous => ({ ...previous, [entry.variant.id]: state }))} onSubmitted={() => { reload(); onFilterChange('cutflow-editing', entry.variant.id) }} /></div> : null}
      </article>
    })}</div>
    {basket.length ? <section className={`cutflow-basket${basketOpen ? ' is-expanded' : ''}`} aria-label="多片段剪辑篮子">
      <div className="cutflow-basket__header" role="toolbar" aria-label="剪辑篮操作"><div className="cutflow-basket__summary" aria-live="polite"><span className="cutflow-basket__count">{basket.length}</span><span><strong>已选 {basket.length} 条原片</strong><small>顺序将作为剪辑输入顺序</small></span></div><button className="icon-button" type="button" aria-expanded={basketOpen} aria-controls="cutflow-basket-details" aria-label={basketOpen ? '收起剪辑篮' : '展开剪辑篮'} onClick={() => setBasketOpen(value => !value)}>{basketOpen ? <ChevronDown size={18} /> : <ChevronUp size={18} />}</button><div className="cutflow-basket__actions"><button className="secondary-button" type="button" onClick={() => { setBasket([]); setBasketMessage('') }}><X size={16} />清空</button><button className="primary-button" type="button" disabled={!canSubmit} onClick={() => { void submitBasket() }} title={multiCutEnabled ? (canSubmit ? undefined : submitHint) : '等待 CUT 多源接入及现场联调后开放'}>{submitting ? <LoaderCircle className="spin" size={17} /> : <Scissors size={17} />}{submitting ? '正在送剪' : (multiCutEnabled ? '送剪' : '送剪暂未开放')}</button></div></div>
      <div id="cutflow-basket-details" className="cutflow-basket__details" aria-hidden={!basketOpen} inert={!basketOpen}>{multiCutEnabled ? <p role="status">送剪后由 CUT 侧执行剪辑，成片回到「剪辑成片」等待审核；费用按所选原片条数计算，预估金额会显示在提交按钮内。</p> : <p role="status">多源剪辑仍在等待 CUT 端合同实现和现场联调，目前不会创建任务。</p>}{brandConflict ? <p role="alert">所选原片分属不同品牌，不能合成同一条片子。请移除其他品牌的素材。</p> : null}{unverifiedBrands > 0 ? <p role="status">有 {unverifiedBrands} 条原片未记录品牌，仍可送剪；服务端会核验，核验不通过时提交会被拒绝。</p> : null}{basket.filter(id => !sources.some(source => source.variant.id === id)).map(id => <p role="alert" key={id}>来源 {id} 已不在当前列表中，请确认后移除。<button type="button" className="compact-button" onClick={() => setBasket(ids => ids.filter(value => value !== id))}>移除无效来源</button></p>)}{basketEntries.filter(item => !validIds.has(item.variant.id)).map(item => <p role="alert" key={item.variant.id}>{item.task.title} · {variantDisplayName(item.variant)} 当前不可送剪，可移除。</p>)}<label><input type="checkbox" checked={policyConfirmed} onChange={event => setPolicyConfirmed(event.target.checked)} />{multiCutEnabled ? '我确认将按当前剪辑策略处理这些原片（会随任务一起提交）。' : '我确认将按当前剪辑策略处理这些原片（仅记录本次页面状态，不会提交）。'}</label><ol>{basketEntries.map((item, index) => <li key={item.variant.id}><span>{index + 1}. {item.task.title} · {variantDisplayName(item.variant)}</span><button type="button" className="compact-button" aria-label={`上移第${index + 1}条原片`} disabled={!index} onClick={() => setBasket(ids => moveCutflowSource(ids, item.variant.id, -1))}><ArrowUp size={16} /></button><button type="button" className="compact-button" aria-label={`下移第${index + 1}条原片`} disabled={index === basketEntries.length - 1} onClick={() => setBasket(ids => moveCutflowSource(ids, item.variant.id, 1))}><ArrowDown size={16} /></button><button type="button" className="compact-button" onClick={() => setBasket(ids => ids.filter(id => id !== item.variant.id))}>移除</button></li>)}</ol></div>{basketMessage ? <p role="status">{basketMessage}</p> : null}
    </section> : null}
    {cutPreview ? <RemoteVideoPreview mediaUrl={cutPreview.mediaUrl} title={cutPreview.title} onClose={() => setCutPreview(null)} /> : null}
    {!visible.length && !visibleMultiTasks.length && !pendingReads ? <EmptyState icon={filter === 'cutflow-finished' ? <Film size={24} /> : <Scissors size={24} />} title={allDisabled ? '剪辑交接尚未启用' : tab.empty} description={allDisabled ? '请联系管理员检查剪辑服务配置。' : keyword ? '当前搜索下没有匹配的视频，可清除搜索再试。' : tab.hint} /> : null}
    <p className="cutflow-library__scope">按已加载任务中的视频分类；更早的视频请使用下方“加载更早记录”。生成原片保留在侧边栏“任务”，剪辑成片在此单独管理。</p>
  </section>
}
