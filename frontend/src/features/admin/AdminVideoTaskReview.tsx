import {
  AlertCircle, ArrowLeft, ChevronLeft, ChevronRight, Clock3, Clapperboard,
  FileText, LoaderCircle, Play, RefreshCw, Search, Workflow,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import {
  adminCutUrl, adminVideoThumbnailUrl, fetchAdminCutObjectUrl,
  fetchAdminVideoObjectUrl, getAdminVideoTasks, getAdminVideoTrace,
} from '../../api'
import { errorMessage } from '../../errorPresentation'
import { PortalVideoModal } from '../../shared/components/Media'
import type {
  AdminCutDeliverable, AdminVideoHealthFilter, AdminVideoRecord,
  AdminVideoSort, AdminVideoTask, AdminVideoTaskPage, AdminVideoTrace,
} from '../../types'
import './adminVideoReview.css'
import './adminVideoTaskReview.css'

type VideoChoice = { kind: 'cut'; task: AdminVideoTask; cut: AdminCutDeliverable }
  | { kind: 'raw'; clip: AdminVideoRecord }

function videoUrl(adminId: string, choice: VideoChoice) {
  return choice.kind === 'cut'
    ? fetchAdminCutObjectUrl(adminId, choice.task.taskId, choice.cut)
    : fetchAdminVideoObjectUrl(adminId, choice.clip.artifactId)
}
function label(clip: AdminVideoRecord) {
  if (clip.variantIndex != null) return clip.variantIndex < 27 ? `Kling 原片 ${String.fromCharCode(64 + clip.variantIndex)}` : `Kling 原片 ${clip.variantIndex}`
  return clip.variant === 'STABLE' ? 'Kling 原片 A'
    : clip.variant === 'DYNAMIC' ? 'Kling 原片 B' : 'Kling 原片'
}
function date(value: string | null) {
  if (!value) return '时间未记录'
  const result = new Date(value)
  return Number.isNaN(result.getTime()) ? '时间未记录' : result.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}
function duration(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return '未记录'
  return seconds < 60 ? `${Math.round(seconds)} 秒` : `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
}
function choiceTitle(choice: VideoChoice) {
  return choice.kind === 'cut'
    ? `${choice.task.title} · CUT 最终成片`
    : `${choice.clip.title} · ${label(choice.clip)}`
}
function cover(task: AdminVideoTask) {
  return task.cut
    ? adminCutUrl(task.taskId, task.cut, 'thumbnail')
    : task.clips[0] ? adminVideoThumbnailUrl(task.clips[0].artifactId) : ''
}
function mainChoice(task: AdminVideoTask): VideoChoice | null {
  return task.cut ? { kind: 'cut', task, cut: task.cut }
    : task.clips[0] ? { kind: 'raw', clip: task.clips[0] } : null
}

function ReviewPlayer({ adminId, choice, onClose }: {
  adminId: string; choice: VideoChoice; onClose: () => void
}) {
  const [src, setSrc] = useState<string | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    let objectUrl: string | null = null
    void videoUrl(adminId, choice).then(url => {
      objectUrl = url
      if (alive) setSrc(url)
      else URL.revokeObjectURL(url)
    }).catch(cause => {
      if (alive) setError(errorMessage(cause, '视频暂时无法播放，请稍后重试。'))
    })
    return () => {
      alive = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [adminId, choice])
  return <PortalVideoModal
    src={src ?? undefined} title={choiceTitle(choice)}
    loadError={error} onClose={onClose}
    aspectRatioHint={choice.kind === 'raw' && choice.clip.width && choice.clip.height
      ? choice.clip.width / choice.clip.height : 9 / 16}
  />
}

function VideoPreview({ adminId, choice, title }: {
  adminId: string; choice: VideoChoice | null; title: string
}) {
  const [source, setSource] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [wanted, setWanted] = useState(false)
  const resourceRef = useRef<string | null>(null)
  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      if (resourceRef.current) URL.revokeObjectURL(resourceRef.current)
    }
  }, [])
  const play = () => {
    if (!choice || wanted) return
    setWanted(true)
    void videoUrl(adminId, choice).then(url => {
      resourceRef.current = url
      if (aliveRef.current) setSource(url)
      else URL.revokeObjectURL(url)
    }).catch(cause => {
      if (aliveRef.current) setError(errorMessage(cause, '视频加载失败'))
    })
  }
  const thumbnail = choice?.kind === 'cut'
    ? adminCutUrl(choice.task.taskId, choice.cut, 'thumbnail')
    : choice?.kind === 'raw' ? adminVideoThumbnailUrl(choice.clip.artifactId) : ''
  return <div className="admin-task-video">
    <h3>{title}</h3>
    {source ? <video controls autoPlay playsInline src={source} /> : choice
      ? <button type="button" className="admin-task-video-cover"
          title={`播放${title}`} aria-label={`播放${title}`}
          onClick={play}>
          <img src={thumbnail} alt={`${title}封面`} />
          {wanted ? <LoaderCircle size={30} className="spin" /> : <Play size={30} fill="currentColor" />}
        </button>
      : <div className="admin-task-video-missing">尚无 CUT 最终成片</div>}
    {error && <p role="alert">{error}</p>}
  </div>
}

function TaskDetail({ adminId, task, position, total, onBack, onPrevious, onNext }: {
  adminId: string; task: AdminVideoTask; position: number; total: number
  onBack: () => void; onPrevious: () => void; onNext: () => void
}) {
  const [traces, setTraces] = useState<Record<string, AdminVideoTrace>>({})
  const [traceError, setTraceError] = useState('')
  useEffect(() => {
    let active = true
    void Promise.allSettled(task.clips.map(c => getAdminVideoTrace(adminId, c.artifactId))).then(results => {
      if (!active) return
      const next: Record<string, AdminVideoTrace> = {}
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') next[task.clips[index].artifactId] = result.value
      })
      setTraces(next)
      if (results.some(r => r.status === 'rejected')) setTraceError('部分原片的历史提示词或耗时暂时无法读取')
    })
    return () => { active = false }
  }, [adminId, task])
  return <section className="admin-task-detail" aria-label="任务巡检详情">
    <header className="admin-task-detail-header">
      <button className="secondary-button" type="button" onClick={onBack}><ArrowLeft size={17} />返回视频目录</button>
      <div className="admin-task-detail-step">
        <button className="secondary-button" type="button" disabled={position === 0} onClick={onPrevious} aria-label="上一条任务"><ChevronLeft size={17} /></button>
        <span>{position + 1} / {total}</span>
        <button className="secondary-button" type="button" disabled={position >= total - 1} onClick={onNext} aria-label="下一条任务"><ChevronRight size={17} /></button>
      </div>
    </header>
    <h2>{task.title}</h2>
    <p className="admin-task-subtitle">{task.brandName || '未归属品牌'} · {task.username} · {date(task.createdAt)} · {task.clips.length} 条原片{task.cut ? ' · CUT 成片已保存' : ' · 暂无成片'}</p>
    <div className="admin-task-video-row">
      <VideoPreview key={`${task.taskId}-cut`} adminId={adminId} choice={task.cut ? { kind: 'cut', task, cut: task.cut } : null} title="CUT 最终成片" />
      {task.clips.map(clip => <VideoPreview key={clip.artifactId} adminId={adminId} choice={{ kind: 'raw', clip }} title={label(clip)} />)}
    </div>
    <section className="admin-task-detail-section">
      <h3><FileText size={18} /> Sol 编排与实际提交 Kling 的提示词</h3>
      {traceError && <p role="status">{traceError}</p>}
      {task.clips.map(clip => {
        const trace = traces[clip.artifactId]
        return <div key={clip.artifactId} className="admin-task-prompt-block">
          <h4>{label(clip)}</h4>
          <div className="admin-task-prompt-pair">
            <div><strong>Sol 原始编排</strong>
              <pre>{trace ? (trace.prompt.plan.masterPrompt || '未记录可核实的 Sol 原始编排') : '正在读取…'}</pre>
            </div>
            <div><strong>Kling 实际接单提示词</strong>
              <pre>{trace ? (trace.klingWireAudit?.exactKlingPrompt || '未找到已核实的供应商接单提示词') : '正在读取…'}</pre>
            </div>
          </div>
        </div>
      })}
    </section>
    <section className="admin-task-detail-section">
      <h3><Clock3 size={18} /> 各环节生产耗时</h3>
      {task.clips.map(clip => {
        const trace = traces[clip.artifactId]
        return <div key={clip.artifactId} className="admin-task-timing">
          <h4>{label(clip)}</h4>
          <div className="admin-task-timing-grid">{trace
            ? trace.durations.map((v, index) => <div key={index}><small>{v.label}</small><strong>{duration(v.seconds)}</strong></div>)
            : <span>正在读取原片时间记录…</span>}</div>
        </div>
      })}
      <div className="admin-task-cut-time">
        <strong>CUT 合成剪辑</strong>
        <span>{task.cut ? `已保存成片 · 时长 ${duration(task.cut.durationSeconds)} · 输出时间 ${date(task.cut.createdAt)}` : `尚无可播放成片 · ${task.cutStatus || '未发现送剪记录'}`}</span>
        <small>CUT 排队与实际剪辑的独立耗时需要对应阶段时间戳；只有建单与更新时间不能可靠拆分。</small>
      </div>
    </section>
  </section>
}

export function AdminVideoTaskReview({ adminId }: { adminId: string }) {
  const [days, setDays] = useState<1 | 7 | 30 | 90>(7)
  const [offset, setOffset] = useState(0)
  const [draft, setDraft] = useState('')
  const [search, setSearch] = useState('')
  const [health, setHealth] = useState<AdminVideoHealthFilter>('all')
  const [sort, setSort] = useState<AdminVideoSort>('newest')
  const [refresh, setRefresh] = useState(0)
  const [page, setPage] = useState<AdminVideoTaskPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeId, setActiveId] = useState<string | null>(null)
  const [player, setPlayer] = useState<VideoChoice | null>(null)
  const galleryScroll = useRef(0)
  useEffect(() => {
    let active = true
    void getAdminVideoTasks(adminId, days, offset, search, health, sort)
      .then(result => { if (active) { setPage(result); setError('') } })
      .catch(cause => { if (active) setError(errorMessage(cause, '视频巡检目录加载失败')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [adminId, days, offset, search, health, sort, refresh])
  const tasks = page?.items || []
  const activeIndex = tasks.findIndex(t => t.taskId === activeId)
  const activeTask = activeIndex >= 0 ? tasks[activeIndex] : null
  const reset = () => { setLoading(true); setOffset(0); setActiveId(null) }
  const openDetail = (taskId: string) => {
    galleryScroll.current = document.scrollingElement?.scrollTop || 0
    setActiveId(taskId)
    if (document.scrollingElement) document.scrollingElement.scrollTop = 0
  }
  const closeDetail = () => {
    setActiveId(null)
    requestAnimationFrame(() => {
      if (document.scrollingElement) document.scrollingElement.scrollTop = galleryScroll.current
    })
  }

  return <section className="admin-video-review admin-task-gallery" aria-label="跨账号视频巡检">
    {activeTask ? <TaskDetail key={activeTask.taskId} adminId={adminId}
      task={activeTask} position={activeIndex} total={tasks.length}
      onBack={closeDetail}
      onPrevious={() => setActiveId(tasks[activeIndex - 1].taskId)}
      onNext={() => setActiveId(tasks[activeIndex + 1].taskId)}
    /> : <>
      <header className="admin-video-header">
        <div><h2>视频巡检</h2><p>每个生产任务一张卡片 · 有成片时点击封面直接播放 CUT 成片</p></div>
        <button className="secondary-button" type="button" onClick={() => { setLoading(true); setRefresh(v => v + 1) }} disabled={loading}><RefreshCw size={16} />刷新</button>
      </header>
      <div className="admin-video-filters">
        <div className="admin-video-days" role="group" aria-label="时间范围">
          {([1, 7, 30, 90] as const).map(d => <button key={d} type="button" className={d === days ? 'is-active' : ''} aria-pressed={d === days}
            onClick={() => { if (d !== days) { reset(); setDays(d) } }}>{d === 1 ? '24 小时' : `${d} 天`}</button>)}
        </div>
        <form onSubmit={e => { e.preventDefault(); reset(); setSearch(draft.trim()); setRefresh(v => v + 1) }}>
          <label className="sr-only" htmlFor="task-gallery-search">搜索品牌、账号、任务名</label>
          <input id="task-gallery-search" placeholder="搜索品牌、账号、任务名" value={draft} maxLength={80} onChange={e => setDraft(e.target.value)} />
          <button className="secondary-button" type="submit"><Search size={16} />搜索</button>
        </form>
      </div>
      {page && <div className="admin-task-filter-row">
        <div role="group" aria-label="异常筛选">
          {(['all', 'critical', 'warning', 'normal'] as const).map(value => <button key={value} type="button"
            aria-pressed={value === health} className={value === health ? 'is-active' : ''}
            onClick={() => { if (value !== health) { reset(); setHealth(value) } }}>{({
              all: '全部任务', critical: '明确异常', warning: '需要检查', normal: '暂无链路异常',
            })[value]}{value !== 'all' ? ` ${page.healthSummary[value]}` : ''}</button>)}
        </div>
        <label>排序 <select aria-label="视频排序" value={sort} onChange={e => { if (e.target.value !== sort) { reset(); setSort(e.target.value as AdminVideoSort) } }}>
          <option value="newest">最近生成优先</option><option value="priority">异常优先</option>
        </select></label>
      </div>}
      <div className="admin-video-summary"><span>本条件下共 <strong>{page?.total ?? '—'}</strong> 个生产任务</span></div>
      {error && <p role="alert" className="service-alert">{error}</p>}
      {loading && <div className="admin-video-loading" role="status"><LoaderCircle className="spin" size={18} />正在加载…</div>}
      {!loading && tasks.length === 0 && !error && <div className="admin-video-empty"><Clapperboard size={25}/><strong>当前没有匹配的视频任务</strong></div>}
      {!loading && !error && <div className="admin-video-grid">
        {tasks.map(task => {
          const selected = mainChoice(task)
          return <article key={task.taskId} className="admin-video-card admin-task-card" data-health={task.severity}>
            <button type="button" className="admin-video-preview" onClick={() => selected && setPlayer(selected)}
              disabled={!selected} aria-label={task.cut ? `播放成片：${task.title}` : `播放原片：${task.title}`}>
              <img src={cover(task)} alt={`${task.title} ${task.cut ? 'CUT 成片' : '原片'}封面`} loading="lazy"
                onError={e => {
                  const image = e.currentTarget
                  if (!image.dataset.fallback && task.clips[0]) {
                    image.dataset.fallback = '1'
                    image.src = adminVideoThumbnailUrl(task.clips[0].artifactId)
                  }
                }} />
              <span className="admin-video-play"><Play size={23} fill="currentColor"/></span>
              <span className="admin-task-cover-label">{task.cut ? 'CUT 成片' : 'Kling 原片'}</span>
              {(task.cut?.durationSeconds ?? task.clips[0]?.durationSeconds) != null &&
                <span className="admin-video-duration">{Math.round(task.cut?.durationSeconds ?? task.clips[0].durationSeconds!)}s</span>}
            </button>
            <div className="admin-video-card-body">
              <strong title={task.title}>{task.title}</strong>
              <span>{task.brandName || '未归属品牌'} · {task.username}</span>
              <small>{date(task.createdAt)} · {task.clips.length} 条原片 · {task.cut ? '成片已保存' : task.cutStatus ? `CUT ${task.cutStatus}` : '暂无成片'}</small>
              {task.severity !== 'normal' && <span className="admin-task-issue"><AlertCircle size={13}/> {task.severity === 'critical' ? '存在明确链路异常' : '存在需要检查的记录'}</span>}
              <div className="admin-video-card-actions">
                <button type="button" className="text-button" onClick={() => openDetail(task.taskId)}><Workflow size={14}/>查看完整详情</button>
              </div>
            </div>
          </article>
        })}
      </div>}
      {page && !loading && <footer className="admin-video-pager">
        <span>第 {Math.floor(offset / 24) + 1} 页 · 共 {page.total} 个任务</span>
        <div>
          <button className="secondary-button" type="button" disabled={offset === 0} onClick={() => { setLoading(true); setOffset(Math.max(offset - 24, 0)) }}><ChevronLeft size={15}/>上一页</button>
          <button className="secondary-button" type="button" disabled={offset + 24 >= page.total} onClick={() => { setLoading(true); setOffset(offset + 24) }}>下一页<ChevronRight size={15}/></button>
        </div>
      </footer>}
      <p className="admin-video-note">管理员只读巡检 · 点击封面直接播放已保存的 CUT 成片；尚无成片时会明确标记并播放原片。不会触发审核或生成。</p>
    </>}
    {player && <ReviewPlayer key={player.kind === 'cut' ? player.cut.batchId + player.cut.outputId : player.clip.artifactId}
      adminId={adminId} choice={player} onClose={() => setPlayer(null)} />}
  </section>
}
