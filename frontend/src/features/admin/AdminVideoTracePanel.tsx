import { AlertTriangle, Clapperboard, Clock3, FileText, Image, Play, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchAdminReferenceObjectUrl, getAdminVideoTrace } from '../../api'
import { errorMessage } from '../../errorPresentation'
import { useMediaObjectUrl } from '../../shared/hooks/useMediaObjectUrl'
import { useDismiss } from '../../shared/hooks/useDismiss'
import type { AdminVideoRecord, AdminVideoTrace } from '../../types'
import './adminVideoTrace.css'

const time = (value: string | null) => {
  if (!value) return '未记录'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '时间格式不明' : date.toLocaleString('zh-CN', { hour12: false })
}

const duration = (value: number | null) => {
  if (value == null || !Number.isFinite(value)) return '未记录'
  if (value < 60) return `${Math.round(value * 10) / 10} 秒`
  const min = Math.floor(value / 60)
  const s = Math.round(value % 60)
  if (min < 60) return `${min} 分 ${s} 秒`
  return `${Math.floor(min / 60)} 小时 ${min % 60} 分 ${s} 秒`
}

function directorLabel(value: string | null) {
  if (value === 'cross_scene_v2') return '智能分镜 / 跨场景 V2'
  if (value === 'reference_guided') return '遵循参考图'
  return value || '未记录'
}

const modeLabel = (value: string | null) =>
  value === 'automatic' ? 'Sol 自动编排' : value === 'custom' ? '用户自定义提示词' : value || '未记录'

function AdminReferenceImage({ adminId, artifactId, assetId, filename }: {
  adminId: string
  artifactId: string
  assetId: string
  filename: string
}) {
  const load = useCallback((id: string) => fetchAdminReferenceObjectUrl(
    adminId, artifactId, id,
  ), [adminId, artifactId])
  const { url, error } = useMediaObjectUrl(assetId, load, '参考图读取失败')
  if (error) return <span>{error}</span>
  return url ? <img src={url} alt={filename} /> : <span>参考图加载中</span>
}

export function AdminVideoTracePanel({ adminId, item, onPlay, onClose }: {
  adminId: string
  item: AdminVideoRecord
  onPlay: () => void
  onClose: () => void
}) {
  const ref = useRef<HTMLElement>(null)
  useDismiss(true, onClose, ref)
  const [trace, setTrace] = useState<AdminVideoTrace | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    void getAdminVideoTrace(adminId, item.artifactId).then(value => {
      if (active) setTrace(value)
    }).catch(cause => {
      if (active) setError(errorMessage(cause, '无法读取这条视频的生产链路。'))
    })
    return () => { active = false }
  }, [adminId, item.artifactId])

  const primaryIntervals = trace?.durations.filter(v => v.seconds != null && (
    v.label === '方案确认至供应商提交（含准备与排队）'
    || v.label === '云端生成至完成（含轮询）'
    || v.label === '云端完成至原片记录建立'
  )) || []
  const longest = [...primaryIntervals].sort((a, b) => (b.seconds ?? 0) - (a.seconds ?? 0))[0]

  const content = <div className="admin-trace-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <section className="admin-trace-panel" ref={ref} role="dialog" aria-modal="true" aria-labelledby="admin-trace-title">
      <header className="admin-trace-header">
        <div><span className="section-kicker">管理员只读 · 生产链路追溯</span>
          <h2 id="admin-trace-title">这条视频是怎么生成的？</h2>
          <p>{item.title} · {item.brandName || '未归属品牌'} · {item.username}</p>
        </div>
        <div className="admin-trace-actions">
          <button type="button" className="secondary-button" onClick={onPlay}><Play size={16} />播放原片</button>
          <button type="button" className="icon-button" aria-label="关闭生产链路" title="关闭" onClick={onClose}><X size={19}/></button>
        </div>
      </header>
      {error && <p role="alert" className="service-alert">{error}</p>}
      {!trace && !error && <div role="status" className="admin-trace-wait">正在读取原始编排和阶段时间记录…</div>}
      {trace && <div className="admin-trace-body">
        <section className="admin-trace-quick">
          <div><small>编排方式</small><strong>{directorLabel(trace.direction.directorMode)}</strong><span>{modeLabel(trace.direction.generationMode)}</span></div>
          <div><small>模型 / 生成时长</small><strong>{trace.direction.model || trace.direction.provider || '未记录'}</strong><span>计划 {trace.direction.durationSeconds ?? '—'} 秒 · {trace.direction.multiShot ? '多分镜' : trace.direction.multiShot === false ? '非多分镜' : '多分镜未记录'}</span></div>
          <div><small>实际提交供应商时间</small><strong>{time(trace.timeline.find(v => v.source === 'generation_attempt.submitted_at')?.at ?? null)}</strong><span>只有系统确认提交时才有此时间</span></div>
          <div><small>任务创建 → 原片记录建立</small><strong>{duration(trace.durations.find(v => v.label.startsWith('任务创建至'))?.seconds ?? null)}</strong><span>两端实测跨度，不代表中间每段均有记录</span></div>
        </section>
        <div className="admin-trace-guidance">
          <Clock3 size={18}/>
          <div><strong>怎么判断哪里慢？</strong>
            <p>{longest ? `可计算区间中较长的是「${longest.label}」：${duration(longest.seconds)}。` : '这条原片暂缺足够时间边界，无法可靠比较阶段耗时。'} 这里是根据已保存时间点推算的区间，不等于已确定瓶颈；需要结合下方完整时间线判断。</p>
          </div>
        </div>
        <section className="admin-trace-section">
          <div className="admin-trace-section-title"><FileText size={17}/><h3>编排方式与提示词</h3></div>
          <dl className="admin-trace-meta">
            <div><dt>生成方式</dt><dd>{modeLabel(trace.direction.generationMode)}</dd></div>
            <div><dt>导演编排模式</dt><dd>{directorLabel(trace.direction.directorMode)}</dd></div>
            <div><dt>镜头切换模式</dt><dd>{trace.direction.cutMode || '未记录'}</dd></div>
            <div><dt>智能分镜设置</dt><dd>{trace.direction.smartStoryboard === null ? '未记录' : trace.direction.smartStoryboard ? '开启' : '关闭'}</dd></div>
            <div><dt>编排器</dt><dd>{trace.prompt.planner || trace.prompt.compiledBy || '未记录'}</dd></div>
            <div><dt>参考图绑定</dt><dd>{trace.references.filter(v => v.bindingEvidence === 'saved_binding').length} 张有明确绑定记录</dd></div>
          </dl>
          {(trace.prompt.plan.creativeIdea || trace.prompt.plan.visualHook || trace.prompt.plan.buyerReason) && <div className="admin-trace-plan">
            <h4>Sol / 编排草案摘要</h4>
            {trace.prompt.plan.creativeIdea && <p><b>创意方向：</b>{trace.prompt.plan.creativeIdea}</p>}
            {trace.prompt.plan.visualHook && <p><b>开场目标：</b>{trace.prompt.plan.visualHook}</p>}
            {trace.prompt.plan.buyerReason && <p><b>购买理由：</b>{trace.prompt.plan.buyerReason}</p>}
          </div>}
          {trace.prompt.plan.shotInstructions && trace.prompt.plan.shotInstructions.length > 0 && <details className="admin-trace-plan">
            <summary>查看已保存的分镜草案（{trace.prompt.plan.shotInstructions.length} 条）</summary>
            {trace.prompt.plan.shotInstructions.map((s, i) => <p key={i}>{i + 1}. {typeof s === 'string' ? s : s ? Object.values(s).join(' · ') : '未记录'}</p>)}
          </details>}
          <div className="admin-trace-prompt-head"><strong>当前数据库保存的最终提示词</strong><span>可核对生成思路与衣服展示要求</span></div>
          {trace.prompt.savedText ? <pre className="admin-trace-prompt">{trace.prompt.savedText}</pre> : <p className="admin-trace-blank">这条记录没有保存提示词正文。</p>}
          {trace.prompt.plan.masterPrompt && trace.prompt.plan.masterPrompt !== trace.prompt.savedText && <details className="admin-trace-technical">
            <summary>查看编排草案原始 Prompt</summary>
            <pre className="admin-trace-prompt">{trace.prompt.plan.masterPrompt}</pre>
          </details>}
          <p className="admin-trace-caution"><AlertTriangle size={14}/> 历史版本没有逐次提交的不可变全文快照，因此不能保证当前提示词与当时发给 Kling 的每个字符完全相同；这不是供应商实际请求报文。</p>
        </section>
        <section className="admin-trace-section">
          <div className="admin-trace-section-title"><Image size={17}/><h3>当时绑定或上传的参考图</h3></div>
          {!trace.references.length && <p className="admin-trace-blank">这条原片没有可追溯的参考图片记录。</p>}
          <div className="admin-trace-reference-grid">
            {trace.references.map(img => <div className="admin-trace-reference" key={img.id}>
              <div className="admin-trace-reference-image">
                {img.available ? <AdminReferenceImage adminId={adminId} artifactId={item.artifactId} assetId={img.id} filename={img.filename} /> : <span>原图已失效</span>}
              </div>
              <strong>{img.imageLabel || `图 ${img.order}`}</strong>
              <small>{img.role === 'STORYBOARD' ? '分镜 / 首帧参考' : img.role === 'PRODUCT' ? '商品参考图' : img.role}</small>
              <small title={img.filename}>{img.filename}</small>
              <small>{img.bindingEvidence === 'saved_binding' ? '有本次视频的图片绑定记录' : '仅有任务素材记录，无法核实是否传入供应商'}</small>
            </div>)}
          </div>
        </section>
        <section className="admin-trace-section">
          <div className="admin-trace-section-title"><Clock3 size={17}/><h3>各环节耗时</h3></div>
          <div className="admin-trace-duration-grid">
            {trace.durations.map((d, i) => <div key={i}>
              <small>{d.label}</small><strong>{duration(d.seconds)}</strong>
              <span>{d.confidence === 'measured' ? '已有明确起止记录' : d.confidence === 'boundary' ? '两个时间点的间隔' : d.confidence === 'aggregate' ? '系统累计值，不能叠加计算' : '客户端上传时报告'}</span>
            </div>)}
          </div>
          <p className="admin-trace-caution">这些区间可能有重叠；不能简单相加。尤其“提交前准备”和“云端生成”包含等待或轮询，需进一步证据才能归因。</p>
        </section>
        <section className="admin-trace-section">
          <div className="admin-trace-section-title"><Clock3 size={17}/><h3>实际生产时间线</h3></div>
          <p className="admin-trace-hint">以下全部是数据库确实保存的阶段时间，按发生顺序排列。未记录的节点不会补造。</p>
          <ol className="admin-trace-timeline">
            {trace.timeline.map((ev, i) => <li key={i}>
              <time>{time(ev.at)}</time><span className="admin-trace-timeline-dot"/>
              <div><strong>{ev.label}</strong>
                <small>{ev.elapsedSeconds != null ? `持续/跨度 ${duration(ev.elapsedSeconds)} · ` : ''}{ev.status || '事件记录'}</small>
              </div>
            </li>)}
          </ol>
          {trace.timeline.length === 0 && <p className="admin-trace-blank">没有足够的阶段时间记录。</p>}
        </section>
        <section className="admin-trace-section">
          <div className="admin-trace-section-title"><Clapperboard size={17}/><h3>送剪记录</h3></div>
          {trace.cut.length ? trace.cut.map((c, i) => <p key={i} className="admin-trace-cut">{c.kind === 'multi' ? '多原片合剪' : '单原片送剪'} · {c.status} · 建单 {time(c.createdAt)} · 最近更新 {time(c.updatedAt)}{c.errorCode ? ` · 异常 ${c.errorCode}` : ''}</p>) : <p className="admin-trace-blank">未找到与这条原片直接关联的 CUT 交接记录；不代表整个任务没有其他送剪记录。</p>}
        </section>
        <section className="admin-trace-section">
          <div className="admin-trace-section-title"><Clapperboard size={17}/><h3>已保存的质量校验</h3></div>
          {trace.validation.length ? trace.validation.map((v, i) => <p key={i} className="admin-trace-cut">{time(v.createdAt)} · {v.status} · {v.coverage}{v.issueCodes.length ? ` · ${v.issueCodes.join('、')}` : ''}</p>) : <p className="admin-trace-blank">没有独立的质量校验记录；不能据此推断视频一定合格或不合格。</p>}
        </section>
        <details className="admin-trace-technical">
          <summary>高级核对信息与数据边界</summary>
          <p>任务 ID：{trace.taskId}</p><p>变体 ID：{trace.variantId}</p><p>生成尝试 ID：{trace.attemptId}</p><p>提示词哈希：{trace.prompt.savedHash || '未记录'}</p>
          {trace.limitations.map((v, i) => <p key={i}>{v}</p>)}
        </details>
      </div>}
    </section>
  </div>
  return createPortal(content, document.body)
}
