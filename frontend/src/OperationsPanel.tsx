import { errorMessage } from './errorPresentation'
import { Activity, AlertTriangle, CircleDollarSign, Gauge, LoaderCircle, Pause, Play, RefreshCw, Timer, WalletCards, Workflow, CheckCircle2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getOperations, setOperationalPause } from './api'
import type { OperationsIssueCategory, OperationsSnapshot } from './types'
import { OperationsIssuesBrowser } from './features/admin/OperationsIssuesBrowser'

const runnerStatusLabels: Record<string, string> = {
  ONLINE: '在线',
  WARNING: '异常',
  PAUSED: '已暂停',
  OFFLINE: '离线',
}

const alertCodeLabels: Record<string, string> = {
  LIBTV_AUTH_REQUIRED: '历史任务连接信息已失效',
  KLING_API_KEY_MISSING: '还没有填写可灵服务密钥',
  KLING_API_AUTHENTICATION_FAILED: '可灵官方服务 密钥验证没通过',
  KLING_API_VERIFICATION_FAILED: '暂时连不上可灵',
  KLING_API_RATE_LIMITED: '可灵现在比较繁忙',
  UNKNOWN_THRESHOLD_EXCEEDED: '状态不明的任务较多，请先检查',
  PREPARED_QUEUE_BELOW_LOW_WATERMARK: '可执行队列任务较少',
  PAID_STATE_UNCERTAIN: '付费结果尚未确认',
  DATA_INTEGRITY_ASSET_CONTAMINATION: '素材关联完整性异常',
  PILOT_BLOCKED_RUNTIME_DRIFT: '运行配置与当前版本不一致',
}

const alertActionLabels: Record<string, string> = {
  RUN_REFRESH_LIBTV_LOGIN: '请检查历史任务认证状态',
  CONFIGURE_KLING_API_KEY: '请填写或更换可灵服务密钥',
  REFRESH_KLING_API_STATUS: '请重新检查可灵连接',
  PAUSE_NEW_PAID_SUBMISSIONS: '请暂停新的付费任务',
  OBSERVE_MATERIALIZATION_CAPACITY: '请检查素材准备是否正常',
  RECONCILE_BEFORE_RETRY: '请先核对供应商回执和扣费记录，禁止盲目重试',
}

const stageLabels: Record<string, string> = {
  analysis: '素材分析',
  cutflow: '剪辑流程（含等待）',
  confirmation: '人工确认',
  materialization: '素材准备',
  queue: '队列等待',
  generation: '视频生成',
  delivery: '下载校验',
  endToEnd: '提交至原片完成',
}

const constraintLabels: Record<string, string> = {
  GLOBAL_PAUSED: '任务调度已暂停',
  GENERATION_CAPACITY: '同时生成的任务已满，请等待',
  RUNNER_DISPATCH: '等待后台开始生成',
  CONFIRMATION: '等待人工确认',
  MATERIALIZATION: '素材准备',
  ANALYSIS: '图片分析',
  DELIVERY: '下载与校验',
  IDLE: '当前无积压',
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '--'
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} 秒`
  if (seconds < 3600) return `${(seconds / 60).toFixed(seconds < 600 ? 1 : 0)} 分钟`
  return `${(seconds / 3600).toFixed(1)} 小时`
}

type PauseScope = 'GLOBAL' | 'PAID_SUBMISSIONS'

export function OperationsPanel({ adminId, onShowTasks, onShowBilling }: {
  adminId: string | null
  onShowTasks?: () => void
  onShowBilling?: () => void
}) {
  const [snapshot, setSnapshot] = useState<OperationsSnapshot | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pendingScope, setPendingScope] = useState<PauseScope | null>(null)
  const [confirmScope, setConfirmScope] = useState<PauseScope | null>(null)
  const [activeIssue, setActiveIssue] = useState<OperationsIssueCategory | null>(null)
  const refresh = useCallback(async () => {
    if (!adminId) return
    try { setSnapshot(await getOperations(adminId)); setError('') }
    catch (cause) { setError(errorMessage(cause, '暂时无法获取运行数据，请稍后刷新。')) }
  }, [adminId])
  useEffect(() => {
    let active = true
    let timer: number | undefined
    const poll = async () => {
      if (!document.hidden) await refresh()
      if (active) timer = window.setTimeout(() => void poll(), 15000)
    }
    timer = window.setTimeout(() => void poll(), 0)
    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [refresh])
  const toggle = async (scope: PauseScope, paused: boolean) => {
    if (!adminId || !snapshot) return
    setPendingScope(scope)
    setNotice('')
    try {
      await setOperationalPause(adminId, snapshot.runtime.version, scope, paused, `${scope.toLowerCase()} changed from operations console`)
      await refresh()
      setNotice(scope === 'GLOBAL'
        ? paused ? '任务调度已暂停，运行中的任务不会被取消。' : '任务调度已恢复，可以领取新的生成任务。'
        : paused ? '新的付费确认已暂停，已提交任务不受影响。' : '付费确认已恢复。')
    } catch (cause) { setError(errorMessage(cause, '暂停状态更新失败')) }
    finally { setPendingScope(null); setConfirmScope(null) }
  }
  if (!adminId) return <section className="ops-empty"><AlertTriangle size={22} /><strong>无法获取管理员身份</strong></section>
  if (!snapshot) return <section className="ops-empty">{error ? <AlertTriangle size={22} /> : <LoaderCircle className="spin" size={22} />}<strong>{error || '正在加载运行状态'}</strong></section>
  const config = snapshot.runtime.config
  const pipeline = snapshot.pipelinePerformance
  const health = snapshot.productionHealth
  const queue = health?.queue
  const blockedCount = queue
    ? queue.blockedBudgetUsd + queue.blockedLegacyLimit + queue.blockedOther
    : null
  const sampledAt = snapshot.sampledAt ? new Date(snapshot.sampledAt) : null
  const busy = pendingScope !== null
  const paidConfirmationBlockedByGlobal = !config.paidSubmissionsPaused && config.globalPaused
  const issueCategories = Number((health?.cloudUnknownAttempts ?? 0) > 0)
    + Number((health?.reservationsAwaitingReview ?? 0) > 0)
    + Number((blockedCount ?? 0) > 0)
  const attentionCategories = issueCategories + Number(snapshot.alerts.length > 0)
  return <section className="operations-console">
    <div className="ops-command-bar">
      <div className="ops-live-status">
        <span className={attentionCategories ? 'ops-live-indicator is-attention' : 'ops-live-indicator'} role="status"><i aria-hidden="true" />{!health ? '指标待同步' : attentionCategories ? `${attentionCategories} 类事项待处理` : '暂无待处理项'}</span>
        <span className="ops-sampled-at">{sampledAt && !Number.isNaN(sampledAt.getTime()) ? `更新于 ${sampledAt.toLocaleTimeString('zh-CN', { hour12: false })}` : '每 15 秒更新'}</span>
        {(config.globalPaused || config.paidSubmissionsPaused) && <span className="ops-live-paused"><AlertTriangle size={13} />生产开关已暂停</span>}
      </div>
      <div className="ops-command-actions">
        {onShowTasks ? <button type="button" className="primary-button" onClick={onShowTasks}>任务中心</button> : null}
        {onShowBilling ? <button type="button" className="secondary-button" onClick={onShowBilling}>用量与费用</button> : null}
        <button type="button" className="secondary-button ops-refresh-action" disabled={busy} onClick={() => void refresh()} aria-label="刷新运行数据"><RefreshCw size={16} />刷新</button>
      </div>
    </div>
    {error && <div className="service-alert" role="alert"><AlertTriangle size={18} /><span>{error}</span></div>}
    {notice && <div className="service-alert service-alert--success" role="status"><Play size={18} /><span>{notice}</span></div>}
    {health && (issueCategories > 0 || snapshot.alerts.length === 0) ? <section className="ops-band ops-issues" aria-label="待处理问题">
      <div className="detail-heading"><div><h3>待处理事项</h3></div>{issueCategories > 0 ? <span className="ops-attention-count">{issueCategories} 类</span> : null}</div>
      {(health.cloudUnknownAttempts > 0 || activeIssue === 'cloudUnknown') ? <div className="ops-issue-row is-danger">
        <AlertTriangle size={19} /><span><strong>云端生成状态待核实 <b>{health.cloudUnknownAttempts} 条</b></strong><small>优先查询供应商回执、扣费与生成结果。不可直接重复提交付费任务。</small></span>
        <button type="button" className="text-button" aria-expanded={activeIssue === 'cloudUnknown'} onClick={() => setActiveIssue(old => old === 'cloudUnknown' ? null : 'cloudUnknown')}>查看全平台明细</button>
      </div> : null}
      {activeIssue === 'cloudUnknown' ? <OperationsIssuesBrowser adminId={adminId} category="cloudUnknown" onClose={() => setActiveIssue(null)} /> : null}
      {(health.reservationsAwaitingReview > 0 || activeIssue === 'reservationReview') ? <div className="ops-issue-row">
        <WalletCards size={19} /><span><strong>费用预留待核对 <b>{health.reservationsAwaitingReview} 条</b></strong><small>关联生成结果：成功 {health.reservationOutcomes.succeeded} · 失败 {health.reservationOutcomes.failed} · 云端待核实 {health.reservationOutcomes.cloudUnknown}{health.reservationOutcomes.other ? ` · 其他 ${health.reservationOutcomes.other}` : ''}；不代表这些费用已经结清。</small></span>
        <button type="button" className="text-button" aria-expanded={activeIssue === 'reservationReview'} onClick={() => setActiveIssue(old => old === 'reservationReview' ? null : 'reservationReview')}>查看全平台明细</button>
      </div> : null}
      {activeIssue === 'reservationReview' ? <OperationsIssuesBrowser adminId={adminId} category="reservationReview" onClose={() => setActiveIssue(null)} /> : null}
      {((blockedCount ?? 0) > 0 || activeIssue === 'blockedPrepared') ? <div className="ops-issue-row">
        <CircleDollarSign size={19} /><span><strong>历史生成受限 <b>{blockedCount} 条</b></strong><small>美元预算 {queue?.blockedBudgetUsd ?? 0} · 旧额度限制 {queue?.blockedLegacyLimit ?? 0} · 其他待处理 {queue?.blockedOther ?? 0}；不会自动恢复付费生成。</small></span>
        <button type="button" className="text-button" aria-expanded={activeIssue === 'blockedPrepared'} onClick={() => setActiveIssue(old => old === 'blockedPrepared' ? null : 'blockedPrepared')}>查看全平台明细</button>
      </div> : null}
      {activeIssue === 'blockedPrepared' ? <OperationsIssuesBrowser adminId={adminId} category="blockedPrepared" onClose={() => setActiveIssue(null)} /> : null}
      {attentionCategories === 0 ? <div className="ops-issues-clear"><CheckCircle2 size={18} />当前没有需要人工处理的异常</div> : null}
    </section> : null}
    {snapshot.alerts.length > 0 ? <section className="ops-safety-alerts" aria-label="安全告警">
      <div className="detail-heading"><h3>安全告警</h3><span>{snapshot.alerts.length} 项</span></div>
      {snapshot.alerts.map((alert) => <div className="ops-row ops-row--alert" key={alert.code}><AlertTriangle size={16} /><span><strong>{alertCodeLabels[alert.code] ?? '有一项运行情况需要检查'}</strong><small>{alertActionLabels[alert.action] ?? '请联系管理员检查任务记录和服务连接。'}</small></span></div>)}
    </section> : null}
    <section className="ops-overview" aria-label="实时产能">
      {!health ? <p className="ops-data-warning" role="status">生产分类指标暂不可用，队列与异常数量不作推断。</p> : null}
      <div className="ops-key-metrics">
        <div><small>生成中 / 上限</small><strong>{snapshot.queues.running}<span> / {config.generationConcurrency.global}</span></strong><em>{Math.round(snapshot.metrics.generationSlotUtilization * 100)}% 并发利用率</em></div>
        <div><small>可执行排队</small><strong>{queue?.actionableTotal ?? '—'}</strong><em>不含预算受限任务</em></div>
        <div><small>24 小时完成原片</small><strong>{pipeline.throughput.completedVideos24h}</strong><em>{pipeline.throughput.completedTasks24h} 个任务</em></div>
        <div><small>在线后台节点</small><strong>{snapshot.metrics.runnerOnlineHosts ?? '—'}</strong><em>{snapshot.metrics.runnerActiveCommandHosts} 台执行中</em></div>
      </div>
    </section>
    <details className="ops-management-details">
      <summary><span>生产控制</span><span className="ops-switch-summary">任务调度：{config.globalPaused ? '已暂停' : '运行中'} <i aria-hidden="true">·</i> 付费确认：{config.paidSubmissionsPaused ? '已暂停' : paidConfirmationBlockedByGlobal ? '受调度暂停影响' : '已开启'}</span></summary>
      <div className="ops-details-inner">
    <div className="ops-control-grid" aria-label="生产控制开关">
      <section className={`ops-control-card ${config.globalPaused ? 'is-paused' : 'is-running'}`}>
        <div className="ops-control-icon"><Workflow size={20} /></div>
        <div className="ops-control-copy">
          <div className="ops-control-title">
            <h3>任务调度</h3>
            <span className={`ops-control-state ${config.globalPaused ? 'is-paused' : 'is-running'}`} role="status" aria-live="polite"><i aria-hidden="true" />{config.globalPaused ? '已暂停' : '运行中'}</span>
          </div>
          <p id="global-scheduling-help">{config.globalPaused ? '不会启动新任务；正在运行的任务继续执行。' : '后台可以开始处理新的生成任务。'}</p>
        </div>
        <button
          className={`${config.globalPaused ? 'primary-button' : 'secondary-button secondary-button--danger'} ops-control-action`}
          disabled={busy}
          aria-describedby="global-scheduling-help"
          onClick={() => setConfirmScope('GLOBAL')}
        >
          {pendingScope === 'GLOBAL' ? <LoaderCircle className="spin" size={16} /> : config.globalPaused ? <Play size={16} /> : <Pause size={16} />}
          {pendingScope === 'GLOBAL' ? (config.globalPaused ? '正在恢复…' : '正在暂停…') : config.globalPaused ? '恢复任务调度' : '暂停任务调度'}
        </button>
      </section>
      <section className={`ops-control-card ${config.paidSubmissionsPaused ? 'is-paused' : paidConfirmationBlockedByGlobal ? 'is-blocked' : 'is-running'}`}>
        <div className="ops-control-icon"><CircleDollarSign size={20} /></div>
        <div className="ops-control-copy">
          <div className="ops-control-title">
            <h3>付费确认</h3>
            <span className={`ops-control-state ${config.paidSubmissionsPaused ? 'is-paused' : paidConfirmationBlockedByGlobal ? 'is-blocked' : 'is-running'}`} role="status" aria-live="polite"><i aria-hidden="true" />{config.paidSubmissionsPaused ? '已暂停' : paidConfirmationBlockedByGlobal ? '已开启 · 暂不可用' : '已开启'}</span>
          </div>
          <p id="paid-confirmation-help">{config.paidSubmissionsPaused ? '拒绝新的付费确认；已提交任务不会取消。' : paidConfirmationBlockedByGlobal ? '开关已开启，但受任务调度暂停影响，当前不可确认。' : '允许新任务确认、预留生成额度并进入生成流程。'}</p>
        </div>
        <button
          className={`${config.paidSubmissionsPaused ? 'primary-button' : 'secondary-button secondary-button--danger'} ops-control-action`}
          disabled={busy}
          aria-describedby="paid-confirmation-help"
          onClick={() => setConfirmScope('PAID_SUBMISSIONS')}
        >
          {pendingScope === 'PAID_SUBMISSIONS' ? <LoaderCircle className="spin" size={16} /> : config.paidSubmissionsPaused ? <Play size={16} /> : <Pause size={16} />}
          {pendingScope === 'PAID_SUBMISSIONS' ? (config.paidSubmissionsPaused ? '正在恢复…' : '正在暂停…') : config.paidSubmissionsPaused ? '恢复付费确认' : '暂停付费确认'}
        </button>
      </section>
    </div>
    {confirmScope ? <div className="ops-confirmation" role="alertdialog" aria-labelledby="ops-confirmation-title" aria-describedby="ops-confirmation-description">
      <span className="ops-confirmation-icon"><AlertTriangle size={19} /></span>
      <div><strong id="ops-confirmation-title">确认{confirmScope === 'GLOBAL' ? config.globalPaused ? '恢复任务调度' : '暂停任务调度' : config.paidSubmissionsPaused ? '恢复付费确认' : '暂停付费确认'}？</strong><p id="ops-confirmation-description">{confirmScope === 'GLOBAL'
        ? config.globalPaused ? '恢复后，后台可以继续处理新的生成任务。' : '暂停后不会启动新任务；已经运行的任务会继续执行。'
        : config.paidSubmissionsPaused ? '恢复后，新任务可以预留生成额度并进入生成队列。' : '暂停后拒绝新的付费确认；已提交任务不会取消。'}</p></div>
      <div><button className="secondary-button" disabled={busy} onClick={() => setConfirmScope(null)}>取消</button><button className={confirmScope === 'GLOBAL' ? config.globalPaused ? 'primary-button' : 'primary-button primary-button--danger' : config.paidSubmissionsPaused ? 'primary-button' : 'primary-button primary-button--danger'} disabled={busy} onClick={() => void toggle(confirmScope, confirmScope === 'GLOBAL' ? !config.globalPaused : !config.paidSubmissionsPaused)}>{busy ? <LoaderCircle className="spin" size={16} /> : null}确认执行</button></div>
    </div> : null}
      </div>
    </details>
    <details className="ops-advanced-details">
      <summary><span>效率与资源详情</span><span>耗时分布 · 生成容量 · 后台节点</span></summary>
      <div className="ops-advanced-inner">
        <div className="ops-queue-facts" aria-label="排队效率">
          <span><small>最久可执行排队</small><strong>{queue?.oldestActionableWaitSeconds == null ? queue ? '无可执行积压' : '—' : formatDuration(queue.oldestActionableWaitSeconds)}</strong></span>
          <span><small>近 30 分钟提交供应商</small><strong>{queue?.launched30m ?? '—'} 条</strong></span>
          <span><small>执行中的电脑</small><strong>{snapshot.metrics.runnerActiveCommandHosts} 台</strong></span>
        </div>
    <section className="ops-band ops-pipeline" aria-labelledby="ops-pipeline-title">
      <div className="detail-heading"><div><span className="section-kicker">效率监控</span><h3 id="ops-pipeline-title">近 {pipeline.windowDays} 天制作耗时</h3></div><span>每 15 秒更新</span></div>
      <div className="ops-pipeline-summary">
        <span><Activity size={17} /><small>24 小时成片 / 任务</small><strong>{pipeline.throughput.completedVideos24h} / {pipeline.throughput.completedTasks24h}</strong></span>
        <span><Timer size={17} /><small>耗时最长的步骤</small><strong>{pipeline.automationBottleneck ? stageLabels[pipeline.automationBottleneck] ?? '正在确认耗时' : '等待样本'}</strong></span>
        <span><Gauge size={17} /><small>当前等待原因</small><strong>{constraintLabels[pipeline.currentConstraint] ?? '正在确认等待原因'}</strong></span>
      </div>
      <div className="ops-stage-grid">
        {Object.entries(stageLabels).map(([key, label]) => {
          const stage = pipeline.stages[key]
          return <div key={key}><span><strong>{label}</strong><small>{stage?.sampleCount ?? 0} 个样本</small></span><span><small>一半 / 九成五任务用时不超过</small><b>{formatDuration(stage?.p50Seconds ?? null)} / {formatDuration(stage?.p95Seconds ?? null)}</b></span></div>
        })}
      </div>
    </section>
    <div className="ops-band"><div className="detail-heading"><div><span className="section-kicker">生成容量</span><h3>同时生成和排队的数量</h3></div><span>设置更新次数：{snapshot.runtime.version}</span></div><div className="ops-capacity"><span>同一任务 <strong>{config.generationConcurrency.sameProject}</strong></span><span>单个用户 <strong>{config.generationConcurrency.perUser}</strong></span><span>全站同时生成 <strong>{config.generationConcurrency.global}</strong></span><span>排队数量下限 / 上限 <strong>{config.preparedQueue.lowWatermark} / {config.preparedQueue.highWatermark}</strong></span></div></div>
    <div className="ops-columns">
      <section className="ops-band"><div className="detail-heading"><div><span className="section-kicker">工作节点</span><h3>后台生成电脑</h3></div><span>{snapshot.runners.length}</span></div>{snapshot.runners.length ? snapshot.runners.map((runner) => {
        const heartbeatAt = new Date(runner.lastHeartbeatAt).getTime()
        const stale = sampledAt && (Number.isNaN(heartbeatAt) || sampledAt.getTime() - heartbeatAt > 120000)
        return <div className="ops-row" key={runner.id}><span><strong>{runner.hostname}</strong><small>{stale ? '心跳超时' : runnerStatusLabels[runner.status] ?? '状态待确认'}</small></span><span>磁盘剩余 {runner.freeDiskPercent.toFixed(1)}%</span></div>
      }) : <p className="ops-muted">暂时没有注册的后台生成节点，请检查后台生成程序。</p>}</section>

    </div>
      </div>
    </details>
  </section>
}
