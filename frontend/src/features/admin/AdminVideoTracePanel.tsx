import { AlertTriangle, Clapperboard, Clock3, FileText, Image, Play, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { adminVideoThumbnailUrl, fetchAdminReferenceObjectUrl, getAdminVideoTrace } from '../../api'
import { errorMessage } from '../../errorPresentation'
import { useMediaObjectUrl } from '../../shared/hooks/useMediaObjectUrl'
import { useDismiss } from '../../shared/hooks/useDismiss'
import type { AdminVideoRecord, AdminVideoTrace } from '../../types'
import './adminVideoTrace.css'
import './adminVideoTraceCompact.css'
import { reviewDate } from './adminReviewTime'

const time = (value: string | null) => {
  if (!value) return '未记录'
  const date = reviewDate(value)
  return !date ? '时间格式不明' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
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

const imageBindingLabel = (value: string | null) => {
  switch (value) {
    case 'converted': return '已转换为 @image_N'
    case 'already_canonical': return '原文已经是标准引用'
    case 'no_recognized_alias': return '无需要转换的引用'
    case 'unbound_or_ambiguous_reference_fallback': return '图片编号无效或不明确，已保留原文'
    case 'length_guard_fallback': return '超过长度限制，已保留原文'
    case 'wire_stage_mismatch': return '无法核对转换阶段与提交文本'
    default: return '旧版审计未记录'
  }
}

const nativeShotLabel = (value: string | null) => {
  switch (value) {
    case 'converted_to_native_shots': return '已转换为原生 shot 格式'
    case 'already_native_shots_in_source': return '输入已采用原生 shot 格式'
    case 'native_shot_pattern_not_losslessly_parseable': return '未转换，保留 Sol 原时间轴'
    case 'native_conversion_skipped_after_image_role_binding': return '格式未通过安全校验，保留原文'
    case 'profile_not_in_native_allowlist': return '当前导演配置不适用'
    case 'multi_shot_disabled': return '多分镜未开启'
    default: return '旧版审计未记录'
  }
}

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
  const maxDuration = Math.max(1, ...(trace?.durations.map(d => d.seconds ?? 0) ?? []))

  const content = <div className="admin-trace-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <section className="admin-trace-panel" ref={ref} role="dialog" aria-modal="true" aria-labelledby="admin-trace-title">
      <header className="admin-trace-header">
        <div><span className="section-kicker">管理员只读 · 生产链路追溯</span>
          <h2 id="admin-trace-title">视频详情 · 生产巡检</h2>
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
        {item.health?.reasons.length ? <section className="admin-trace-health" aria-label="发现的异常环节">
          <h3>系统发现 {item.health.reasons.length} 项需要关注的链路记录</h3>
          {item.health.reasons.map(issue => <div key={issue.code} className={`admin-trace-health-issue is-${issue.level}`}>
            <strong>{issue.title} · {issue.stage}</strong>
            <p>{issue.detail}</p>
            <p><b>建议：</b>{issue.nextStep}</p>
            <small>判断依据：{issue.evidence}</small>
          </div>)}
          <p className="admin-trace-health-hint">只依据系统记录标记；不能代表已经自动检测出视频画面的穿帮或印花问题。</p>
        </section> : null}
        <div className="admin-trace-hero">
          <button type="button" className="admin-trace-poster" data-orientation={item.width && item.height && item.width > item.height ? 'landscape' : 'portrait'} onClick={onPlay} aria-label="播放当前原片">
            <img src={adminVideoThumbnailUrl(item.artifactId)} alt={`${item.title} 原片封面`} />
            <span className="admin-trace-poster-play"><Play size={21} fill="currentColor" /></span>
            <span className="admin-trace-poster-caption">点击播放原片</span>
          </button>
          <div className="admin-trace-hero-content">
            <div className="admin-trace-hero-heading">
              <strong>{item.variant === 'STABLE' ? '原片 A' : item.variant === 'DYNAMIC' ? '原片 B' : '生成原片'} · {item.durationSeconds != null ? duration(item.durationSeconds) : '时长未记录'}</strong>
              <span className={`admin-trace-health-status${item.health?.severity === 'critical' ? ' is-critical' : item.health?.severity === 'warning' ? ' is-warning' : ''}`}>
                {item.health?.severity === 'critical' ? '发现明确异常' : item.health?.severity === 'warning' ? '存在待检查项' : item.health?.severity === 'normal' ? '暂无链路异常' : '链路状态未分类'}
              </span>
            </div>
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
          </div>
        </div>
        <details className="admin-trace-disclosure">
          <summary><FileText size={17}/><h3>生成参数与提示词</h3></summary>
          <div className="admin-trace-disclosure-content">
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
          {trace.klingWireAudit ? <div className="admin-trace-plan" aria-label="Kling 实际提交审计">
            <h4>Kling 实际提交证据 · {time(trace.klingWireAudit.recordedAtUtc)}</h4>
            <p>已核实为此片段的供应商接单快照，非根据当前规则推算。</p>
            <dl className="admin-trace-meta">
              <div><dt>图片引用转换</dt><dd>{imageBindingLabel(trace.klingWireAudit.imageReferenceSourceConversion)}</dd></div>
              <div><dt>实际图号绑定</dt><dd>{trace.klingWireAudit.imageReferenceBindingVerified === true ? '已校验' : trace.klingWireAudit.imageReferenceBindingVerified === false ? '未通过校验' : '旧版未记录'}</dd></div>
              <div><dt>原生分镜转换</dt><dd>{nativeShotLabel(trace.klingWireAudit.nativeShotConversion)}</dd></div>
            </dl>
            {trace.klingWireAudit.imageReferenceSourceAliasesChanged != null && <p>Sol 原文被规范化的图片别名：{trace.klingWireAudit.imageReferenceSourceAliasesChanged} 处。</p>}
            <details className="admin-trace-technical">
              <summary>查看真正发送给 Kling 的提示词（已确认接单）</summary>
              <pre className="admin-trace-prompt">{trace.klingWireAudit.exactKlingPrompt}</pre>
            </details>
          </div> : <p className="admin-trace-caution"><AlertTriangle size={14}/> 未找到与本片段供应商接单记录匹配的原始请求审计；只能展示数据库保存的提示词，不能确认图片或分镜是否实际转换。</p>}
          <p className="admin-trace-caution"><AlertTriangle size={14}/> 上方“当前数据库保存”的提示词可能不同于当时发出的实际请求，只有已核实的 Kling 提交快照能证明发送文本。</p>
          </div>
        </details>
        <section className="admin-trace-section admin-trace-reference-section">
          <div className="admin-trace-section-title"><Image size={17}/><h3>商品图与分镜参考图</h3></div>
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
            {trace.durations.map((d, i) => <div key={i} className={`admin-trace-duration-row${d.seconds == null ? ' is-missing' : d.seconds === maxDuration ? ' is-longest' : ''}`}>
              <div className="admin-trace-duration-top"><small>{d.label}</small><strong>{duration(d.seconds)}</strong></div>
              <span className="admin-trace-duration-track" aria-hidden="true"><span className="admin-trace-duration-fill" style={{ width: `${d.seconds == null ? 0 : Math.min(100, Math.max(0, d.seconds / maxDuration * 100))}%` }} /></span>
              <span>{d.confidence === 'measured' ? '已有明确起止记录' : d.confidence === 'boundary' ? '两个时间点的间隔' : d.confidence === 'aggregate' ? '系统累计值，不能叠加计算' : '客户端上传时报告'}</span>
            </div>)}
          </div>
          <p className="admin-trace-caution">横条按已记录区间的时长等比例显示，<b>不是任务进度</b>。区间可能重叠，不能相加；等待和轮询也不等于供应商实际处理时间。</p>
        </section>
        <details className="admin-trace-disclosure">
          <summary><Clock3 size={17}/><h3>完整生产时间线 · {trace.timeline.length} 条记录</h3></summary>
          <div className="admin-trace-disclosure-content">
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
          </div>
        </details>
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
