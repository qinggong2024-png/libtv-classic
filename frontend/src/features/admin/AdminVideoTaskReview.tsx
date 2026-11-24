import {
  AlertCircle, ArrowLeft, ChevronLeft, ChevronRight, Clapperboard,
  FileText, Image, LoaderCircle, Play, RefreshCw, Search, Workflow,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  adminCutUrl, adminVideoThumbnailUrl, fetchAdminCutObjectUrl,
  fetchAdminReferenceObjectUrl, fetchAdminVideoObjectUrl, getAdminVideoTasks, getAdminVideoTrace,
} from '../../api'
import { errorMessage } from '../../errorPresentation'
import { PortalVideoModal } from '../../shared/components/Media'
import { useMediaObjectUrl } from '../../shared/hooks/useMediaObjectUrl'
import type {
  AdminCutDeliverable, AdminVideoHealthFilter, AdminVideoRecord, AdminVideoReference,
  AdminVideoSort, AdminVideoTask, AdminVideoTaskPage, AdminVideoTrace,
} from '../../types'
import { AdminReviewTimeline } from './AdminReviewTimeline'
import { reviewDate, taskCreationTime } from './adminReviewTime'
import './adminVideoReview.css'
import './adminVideoTaskReview.css'
import './adminVideoTaskReviewCompact.css'

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
  const result = reviewDate(value)
  return !result ? '时间未记录' : result.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}
function ReferenceImage({ adminId, clipId, reference }: {
  adminId: string; clipId: string; reference: AdminVideoReference
}) {
  const load = useCallback((assetId: string) => (
    fetchAdminReferenceObjectUrl(adminId, clipId, assetId)
  ), [adminId, clipId])
  const { url, error } = useMediaObjectUrl(reference.id, load, '参考图读取失败', reference.available)
  if (!reference.available) return <span>原图已失效</span>
  if (error) return <span role="status">{error}</span>
  return url ? <img src={url} alt={reference.filename} loading="lazy" /> : <span>正在加载参考图…</span>
}

function referenceName(reference: AdminVideoReference) {
  return reference.role === 'PRODUCT' ? '商品图'
    : reference.role === 'STORYBOARD' ? '分镜 / 首帧图' : '其他参考图'
}

function savedParameter(value: string | boolean | number | null | undefined) {
  if (value == null || value === '') return '未记录'
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  return String(value)
}

function ReferenceSection({ adminId, clip, trace, failed }: {
  adminId: string; clip: AdminVideoRecord; trace?: AdminVideoTrace; failed: boolean
}) {
  if (!trace) return <div className="admin-task-reference-clip">
    <h4>{label(clip)}</h4>
    <p className="admin-task-reference-empty">{failed ? '本条原片的参考图与参数读取失败' : '正在读取上传参考图与参数…'}</p>
  </div>
  const refs = [...trace.references].sort((a, b) => {
    const priority = (role: string) => role === 'PRODUCT' ? 0 : role === 'STORYBOARD' ? 1 : 2
    return priority(a.role) - priority(b.role) || a.order - b.order
  })
  const savedBindings = refs.filter(ref => ref.bindingEvidence === 'saved_binding').length
  const params = [
    ['制作方式', savedParameter(trace.direction.generationMode)],
    ['导演编排', savedParameter(trace.direction.directorMode)],
    ['生成模型', savedParameter(trace.direction.model || trace.direction.provider)],
    ['原片时长', trace.direction.durationSeconds == null ? '未记录' : `${trace.direction.durationSeconds} 秒`],
    ['智能分镜', savedParameter(trace.direction.smartStoryboard)],
    ['多分镜', savedParameter(trace.direction.multiShot)],
    ['镜头切换', savedParameter(trace.direction.cutMode)],
    ['提供商通道', savedParameter(trace.direction.providerBackend)],
  ]
  return <div className="admin-task-reference-clip">
    <div className="admin-task-reference-heading">
      <h4>{label(clip)}</h4>
      <span>{refs.length} 张可追溯参考图 · {savedBindings} 张有明确绑定</span>
    </div>
    {refs.length ? <div className="admin-task-reference-grid">
      {refs.map(ref => <figure className="admin-task-reference-card" key={`${clip.artifactId}:${ref.id}`}>
        <div className="admin-task-reference-image">
          <ReferenceImage adminId={adminId} clipId={clip.artifactId} reference={ref} />
        </div>
        <figcaption>
          <strong>{referenceName(ref)} · {ref.imageLabel || `图 ${ref.order}`}</strong>
          <small title={ref.filename}>{ref.filename}</small>
          <span data-binding={ref.bindingEvidence}>
            {ref.bindingEvidence === 'saved_binding' ? '本片段有保存的图片绑定' : '仅找到任务图片，实际提交未核实'}
          </span>
        </figcaption>
      </figure>)}
    </div> : <p className="admin-task-reference-empty">没有可追溯的生成参考图；这不代表当时没有上传图片。</p>}
    <details className="admin-task-parameter-disclosure">
      <summary>查看本条原片的生成参数 · {params.length} 项</summary>
      <div className="admin-task-parameter-list">
        {params.map(([name, value]) => <div key={name}><small>{name}</small><strong>{value}</strong></div>)}
      </div>
    </details>
    {trace.klingWireAudit && <p className="admin-task-reference-audit">
      Kling 接单快照：图片绑定{trace.klingWireAudit.imageReferenceBindingVerified === true ? '已校验' : trace.klingWireAudit.imageReferenceBindingVerified === false ? '未通过校验' : '未记录校验结果'}
    </p>}
  </div>
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

function VideoPreviewContent({ adminId, choice, title }: {
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
  return <>
    {source ? <video controls autoPlay playsInline src={source} /> : choice
      ? <button type="button" className="admin-task-video-cover"
          title={`播放${title}`} aria-label={`播放${title}`}
          onClick={play}>
          <img src={thumbnail} alt={`${title}封面`} />
          {wanted ? <LoaderCircle size={30} className="spin" /> : <Play size={30} fill="currentColor" />}
        </button>
      : <div className="admin-task-video-missing">尚无 CUT 最终成片</div>}
    {error && <p className="admin-task-video-error" role="alert">{error}</p>}
  </>
}

function TaskDetail({ adminId, task, position, total, onBack, onPrevious, onNext }: {
  adminId: string; task: AdminVideoTask; position: number; total: number
  onBack: () => void; onPrevious: () => void; onNext: () => void
}) {
  const [traces, setTraces] = useState<Record<string, AdminVideoTrace>>({})
  const [traceError, setTraceError] = useState('')
  const [traceLoading, setTraceLoading] = useState(true)
  const [playing, setPlaying] = useState<VideoChoice | null>(mainChoice(task))
  const [inspectedClipId, setInspectedClipId] = useState(task.clips[0]?.artifactId ?? '')
  useEffect(() => {
    let active = true
    void Promise.allSettled(task.clips.map(c => getAdminVideoTrace(adminId, c.artifactId))).then(results => {
      if (!active) return
      const next: Record<string, AdminVideoTrace> = {}
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') next[task.clips[index].artifactId] = result.value
      })
      setTraces(next)
      if (results.some(result => result.status === 'rejected')) {
        setTraceError('部分原片的历史信息无法读取')
      }
      setTraceLoading(false)
    })
    return () => { active = false }
  }, [adminId, task])
  const clip = task.clips.find(c => c.artifactId === inspectedClipId) || task.clips[0]
  const trace = clip ? traces[clip.artifactId] : undefined
  const selectClip = (item: AdminVideoRecord) => {
    setPlaying({ kind: 'raw', clip: item })
    setInspectedClipId(item.artifactId)
  }
  const currentVideoKey = playing?.kind === 'cut'
    ? `cut-${playing.cut.batchId}-${playing.cut.outputId}`
    : playing?.kind === 'raw' ? playing.clip.artifactId : 'none'
  return <section className="admin-task-detail" aria-label="任务巡检详情">
    <header className="admin-task-detail-header">
      <button className="secondary-button" type="button" onClick={onBack}><ArrowLeft size={17} />返回视频目录</button>
      <div className="admin-task-detail-step">
        <button className="secondary-button" type="button" disabled={position === 0} onClick={onPrevious} aria-label="上一条任务"><ChevronLeft size={17} /></button>
        <span>{position + 1} / {total}</span>
        <button className="secondary-button" type="button" disabled={position >= total - 1} onClick={onNext} aria-label="下一条任务"><ChevronRight size={17} /></button>
      </div>
    </header>
    <div className="admin-task-identity">
      <h2 title={task.title}>{task.brandName || '视频任务'} · 任务巡检</h2>
      <span>{task.username} · {date(taskCreationTime(task, Object.values(traces)))} · {task.clips.length} 条原片</span>
      {task.severity !== 'normal' && <span className="admin-task-identity-risk" data-tone={task.severity}>{task.severity === 'critical' ? '存在异常' : '需要检查'}</span>}
    </div>
    <div className="admin-task-layout">
      <div className="admin-task-media-column">
        <div className="admin-task-video">
          <h3>{playing?.kind === 'cut' ? 'CUT 成片' : playing?.kind === 'raw' ? label(playing.clip) : '无视频'}</h3>
          <div className="admin-task-video-frame">
            <VideoPreviewContent key={currentVideoKey} adminId={adminId} choice={playing}
              title={playing?.kind === 'cut' ? 'CUT 成片' : playing?.kind === 'raw' ? label(playing.clip) : '无视频'} />
          </div>
        </div>
        <div className="admin-task-media-strip" role="group" aria-label="切换预览视频">
          {task.cut && <button type="button" className={playing?.kind === 'cut' ? 'is-active' : ''}
            aria-pressed={playing?.kind === 'cut'} onClick={() => setPlaying({ kind: 'cut', task, cut: task.cut! })}>
            <img src={adminCutUrl(task.taskId, task.cut, 'thumbnail')} alt="CUT 成片封面" />
            <span>CUT 成片</span>
          </button>}
          {task.clips.map(item => <button type="button" key={item.artifactId}
            className={playing?.kind === 'raw' && playing.clip.artifactId === item.artifactId ? 'is-active' : ''}
            aria-pressed={playing?.kind === 'raw' && playing.clip.artifactId === item.artifactId}
            onClick={() => selectClip(item)}>
            <img src={adminVideoThumbnailUrl(item.artifactId)} alt={`${label(item)}封面`} />
            <span>{label(item)}</span>
          </button>)}
        </div>
      </div>
      <div className="admin-task-inspector">
        <AdminReviewTimeline task={task} traces={Object.values(traces)} loading={traceLoading} />
        <section className="admin-task-detail-section" aria-label="上传参考图与生成参数">
          <header className="admin-task-panel-heading">
            <h3><Image size={17} /> 生成参数与参考图</h3>
            {task.clips.length > 1 && <div className="admin-task-clip-switch" role="group" aria-label="查看哪条原片的生成资料">
              {task.clips.map(item => <button key={item.artifactId} type="button"
                className={clip?.artifactId === item.artifactId ? 'is-active' : ''}
                aria-pressed={clip?.artifactId === item.artifactId}
                onClick={() => setInspectedClipId(item.artifactId)}>{label(item).replace('Kling 原片 ', '')}</button>)}
            </div>}
          </header>
          {clip && <ReferenceSection adminId={adminId} clip={clip}
            trace={trace} failed={!traceLoading && Boolean(traceError)} />}
        </section>
        <section className="admin-task-detail-section" aria-label="Sol和Kling提示词">
          <h3><FileText size={17} /> 提示词 {clip ? `· ${label(clip)}` : ''}</h3>
          {traceError && <p role="status" className="admin-task-reference-empty">{traceError}</p>}
          <div className="admin-task-prompt-pair">
            <details>
              <summary>{trace?.prompt.plan.masterPrompt ? 'Sol 编排草案' : '已保存编排提示词'}</summary>
              <pre>{trace ? (trace.prompt.plan.masterPrompt || trace.prompt.savedText || '未记录编排提示词') : traceLoading ? '读取中…' : '未记录'}</pre>
            </details>
            <details>
              <summary>Kling 实际接单提示词</summary>
              <pre>{trace ? (trace.klingWireAudit?.exactKlingPrompt || '未找到已核实的 Kling 接单快照') : traceLoading ? '读取中…' : '未记录'}</pre>
            </details>
          </div>
        </section>
      </div>
    </div>
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
        <div><h2>视频巡检</h2></div>
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
            <button type="button" className="admin-video-preview" data-orientation={selected?.kind === 'raw' && selected.clip.width && selected.clip.height && selected.clip.width > selected.clip.height ? 'landscape' : 'portrait'} onClick={() => selected && setPlayer(selected)}
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
              <small>{taskCreationTime(task) ? '创建 ' + date(taskCreationTime(task)) : '原片保存 ' + date(task.latestRawAt ?? task.createdAt)} · {task.clips.length} 条原片 · {task.cut ? '成片已保存' : task.cutStatus ? `CUT ${task.cutStatus}` : '暂无成片'}</small>
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

    </>}
    {player && <ReviewPlayer key={player.kind === 'cut' ? player.cut.batchId + player.cut.outputId : player.clip.artifactId}
      adminId={adminId} choice={player} onClose={() => setPlayer(null)} />}
  </section>
}
