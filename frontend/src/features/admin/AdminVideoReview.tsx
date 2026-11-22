import { ChevronLeft, ChevronRight, Clapperboard, LoaderCircle, Play, RefreshCw, Search, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { adminVideoThumbnailUrl, fetchAdminVideoObjectUrl, getAdminVideos } from '../../api'
import { errorMessage } from '../../errorPresentation'
import { VideoModal } from '../../shared/components/Media'
import { AdminVideoTracePanel } from './AdminVideoTracePanel'
import type { AdminVideoPage, AdminVideoRecord } from '../../types'
import './adminVideoReview.css'

function ReviewedVideo({ adminId, item, onClose }: {
  adminId: string
  item: AdminVideoRecord
  onClose: () => void
}) {
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    let objectUrl: string | null = null
    void fetchAdminVideoObjectUrl(adminId, item.artifactId).then((source) => {
      objectUrl = source
      if (active) setUrl(source)
      else URL.revokeObjectURL(source)
    }).catch((cause) => {
      if (active) setError(errorMessage(cause, '原片已失效或暂时无法播放。'))
    })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [adminId, item.artifactId])
  return <VideoModal src={url ?? undefined} title={item.title} loadError={error} onClose={onClose} />
}

function readableDate(text: string | null): string {
  if (!text) return '日期未记录'
  const date = new Date(text)
  return Number.isNaN(date.valueOf())
    ? '日期未记录'
    : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

export function AdminVideoReview({ adminId }: { adminId: string }) {
  const [days, setDays] = useState<1 | 7 | 30 | 90>(7)
  const [offset, setOffset] = useState(0)
  const [draft, setDraft] = useState('')
  const [search, setSearch] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [page, setPage] = useState<AdminVideoPage | null>(null)
  const [selected, setSelected] = useState<AdminVideoRecord | null>(null)
  const [inspected, setInspected] = useState<AdminVideoRecord | null>(null)

  useEffect(() => {
    let active = true
    void getAdminVideos(adminId, days, offset, search).then((result) => {
      if (active) { setPage(result); setError('') }
    }).catch((cause) => {
      if (active) setError(errorMessage(cause, '最近视频加载失败，请刷新重试。'))
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [adminId, days, offset, search, refreshKey])

  const changeWindow = (value: 1 | 7 | 30 | 90) => {
    if (value === days) return
    setLoading(true)
    setOffset(0)
    setDays(value)
  }

  return <section className="admin-video-review" aria-label="全平台视频质量巡检">
    <header className="admin-video-header">
      <div>
        <span className="section-kicker">全平台质检 · 管理员专用</span>
        <h2>最近生成的视频</h2>
        <p>可以查看各品牌和账号的实际生成原片，发现印花、手部、动作、镜头及节奏问题。这里只用于观察，不会改变其他账号的任务。</p>
      </div>
      <button className="secondary-button" type="button" disabled={loading} onClick={() => { setLoading(true); setRefreshKey(v => v + 1) }}><RefreshCw size={16} />刷新</button>
    </header>
    <div className="admin-video-filters">
      <div className="admin-video-days" role="group" aria-label="查看最近视频时间范围">
        {([1, 7, 30, 90] as const).map((d) => <button type="button" key={d} className={days === d ? 'is-active' : ''} aria-pressed={days === d} onClick={() => changeWindow(d)}>{d === 1 ? '24 小时' : `${d} 天`}</button>)}
      </div>
      <form onSubmit={(event) => {
        event.preventDefault()
        setLoading(true)
        setOffset(0)
        setSearch(draft.trim())
      }}>
        <label className="sr-only" htmlFor="admin-video-search">搜索品牌、账号、任务名</label>
        <input id="admin-video-search" value={draft} onChange={e => setDraft(e.target.value)} maxLength={80} placeholder="搜索品牌、账号、任务名" />
        <button className="secondary-button" type="submit"><Search size={16} />搜索</button>
      </form>
    </div>
    <div className="admin-video-summary">
      <span>本条件下共 <strong>{page?.total ?? '—'}</strong> 条已保存原片</span>
      <span>建议检查：商品印花完整度 · 手部穿帮 · 动作自然度 · 展示时长 · 镜头节奏</span>
    </div>
    {error && <p role="alert" className="service-alert">{error}</p>}
    {loading ? <div className="admin-video-loading" role="status"><LoaderCircle size={19} className="spin" />正在加载最近视频…</div> : null}
    {!loading && page?.items.length === 0 && !error ? <div className="admin-video-empty"><Clapperboard size={27} /><strong>这个时间段没有匹配的已保存原片</strong><small>可切换 30 天或清除搜索条件</small></div> : null}
    {!loading && !error ? <div className="admin-video-grid">
      {page?.items.map((item) => <article key={item.artifactId} className="admin-video-card">
        <button type="button" className="admin-video-preview" onClick={() => setSelected(item)} title={`播放 ${item.title}`} aria-label={`播放视频：${item.title}`}>
          <img src={adminVideoThumbnailUrl(item.artifactId)} alt={`${item.title} 视频首帧`} loading="lazy" />
          <span className="admin-video-play"><Play size={23} fill="currentColor" /></span>
          {item.durationSeconds != null ? <span className="admin-video-duration">{Math.round(item.durationSeconds)}s</span> : null}
        </button>
        <div className="admin-video-card-body">
          <strong title={item.title}>{item.title}</strong>
          <span>{item.brandName || '未归属品牌'} · {item.username}</span>
          <small>{readableDate(item.createdAt)} · {item.variant} · {item.provider}</small>
          <div className="admin-video-card-actions">
            <button type="button" className="text-button" onClick={() => setSelected(item)}>播放检查</button>
            <button type="button" className="text-button" onClick={() => setInspected(item)}><Workflow size={14} />查看生产链路</button>
          </div>
        </div>
      </article>)}
    </div> : null}
    {page && !loading ? <footer className="admin-video-pager">
      <span>第 {Math.floor(offset / 24) + 1} 页 · 共 {page.total} 条</span>
      <div>
        <button type="button" className="secondary-button" disabled={offset === 0} onClick={() => { setLoading(true); setOffset(Math.max(0, offset - 24)) }}><ChevronLeft size={15} />上一页</button>
        <button type="button" className="secondary-button" disabled={offset + 24 >= page.total} onClick={() => { setLoading(true); setOffset(offset + 24) }}>下一页<ChevronRight size={15} /></button>
      </div>
    </footer> : null}
    <p className="admin-video-note">只读巡检：不会修改原账号任务、视频审核状态、费用或提交新的生成请求。仅显示有服务端保存副本的原片。</p>
    {inspected ? <AdminVideoTracePanel key={inspected.artifactId} adminId={adminId} item={inspected} onPlay={() => setSelected(inspected)} onClose={() => setInspected(null)} /> : null}
    {selected ? <ReviewedVideo key={selected.artifactId} adminId={adminId} item={selected} onClose={() => setSelected(null)} /> : null}
  </section>
}
