import { ChevronDown, ChevronLeft, ChevronRight, LoaderCircle, Search, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getOperationsIssues } from '../../api'
import { errorMessage } from '../../errorPresentation'
import type { OperationsIssueCategory, OperationsIssuePage, OperationsIssueRecord } from '../../types'

const descriptions: Record<OperationsIssueCategory, { title: string; summary: string }> = {
  cloudUnknown: {
    title: '云端生成待核实',
    summary: '逐条核对供应商任务 ID、回执和扣费，未确认之前不要重新付费提交。',
  },
  reservationReview: {
    title: '费用预留待核对',
    summary: '这里只显示历史财务预留状态，不代表实际已扣费或退款；按记录核对。',
  },
  blockedPrepared: {
    title: '历史生成受限',
    summary: '已排除可执行队列，需检查账号预算或历史限制，不会自动重新生成。',
  },
}

function issueAdvice(category: OperationsIssueCategory, item: OperationsIssueRecord): {
  title: string
  meaning: string
  nextStep: string
  urgency: 'now' | 'later' | 'none'
} {
  if (category === 'cloudUnknown' || item.attemptStatus === 'UNKNOWN_AFTER_SUBMIT') {
    return {
      title: '生成结果还没有确认',
      meaning: '系统暂时不知道供应商是否已接单、生成或扣费。',
      nextStep: '需要管理员或技术人员根据云端任务号核对供应商记录；核实前不要再次付费生成。',
      urgency: 'now',
    }
  }
  if (category === 'reservationReview') {
    if (item.attemptStatus === 'SUCCEEDED') return {
      title: '视频已生成，费用记录还需要核对',
      meaning: '视频结果是成功的；“费用待核对”不等于视频生成失败。',
      nextStep: '不需要重新生成。安排财务或技术人员核对真实扣费记录即可。',
      urgency: 'later',
    }
    if (item.attemptStatus === 'FAILED_TERMINAL') return {
      title: '生成失败，是否扣费仍未核实',
      meaning: '当前生成记录已失败，但不能单凭失败状态确认费用是否退回。',
      nextStep: '先核对供应商账单与失败原因，再决定是否需要处理。',
      urgency: 'now',
    }
    return {
      title: '费用记录暂时没结清',
      meaning: '这是账务核对项，不一定代表当前视频有问题。',
      nextStep: '安排账号与费用管理员核对，不要仅凭这个提示再次生成。',
      urgency: 'later',
    }
  }
  const budget = item.phaseReasonCode === 'USD_DAILY_BUDGET_EXCEEDED'
  return {
    title: budget ? '这条任务当时遇到了预算限制' : '这条任务当时遇到了生成额度限制',
    meaning: '这是旧任务的受限记录，不是现在正在排队的任务。',
    nextStep: '如果已经不需要生成，可以不操作；如果仍需要视频，先检查账号预算或额度，再人工决定如何处理。',
    urgency: 'none',
  }
}

const dateText = (value: string | null) => {
  if (!value) return '未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

const display = (value: string | number | null | undefined) => value == null || value === '' ? '未记录' : String(value)

function IssueDetail({ item }: { item: OperationsIssueRecord }) {
  return <details className="ops-technical-details">
    <summary>查看技术信息（仅排查时需要）</summary>
    <dl className="ops-issue-detail-grid">
    <div><dt>任务 ID</dt><dd title={item.taskId}>{item.taskId}</dd></div>
    <div><dt>生成记录 ID</dt><dd title={item.attemptId}>{item.attemptId}</dd></div>
    <div><dt>片段</dt><dd>{display(item.variantType)}</dd></div>
    <div><dt>任务状态 / 原因</dt><dd>{item.taskPhase} / {display(item.phaseReasonCode)}</dd></div>
    <div><dt>生成服务 / 状态</dt><dd>{item.provider} / {display(item.providerStatus)}</dd></div>
    <div><dt>云端任务 ID</dt><dd>{display(item.providerTaskId)}</dd></div>
    <div><dt>最近错误代码</dt><dd>{display(item.lastErrorCode)}</dd></div>
    <div><dt>生成异常</dt><dd>{display(item.errorMessage)}</dd></div>
    <div><dt>最后核实时间</dt><dd>{dateText(item.reconciledAt)}</dd></div>
    <div><dt>核实异常</dt><dd>{display(item.reconcileError)}</dd></div>
    <div><dt>提交时间</dt><dd>{dateText(item.submittedAt)}</dd></div>
    <div><dt>原始任务创建</dt><dd>{dateText(item.taskCreatedAt)}</dd></div>
    <div><dt>生成费用判定</dt><dd>{display(item.chargeStatus)}</dd></div>
    <div><dt>费用预留状态</dt><dd>{display(item.reservationStatus)}</dd></div>
    <div><dt>费用记录 ID</dt><dd>{display(item.reservationId)}</dd></div>
    <div><dt>预估 / 实际生成额度</dt><dd>{display(item.estimatedPoints)} / {display(item.actualPoints)}</dd></div>
    <div><dt>费用记录创建</dt><dd>{dateText(item.reservationCreatedAt)}</dd></div>
    </dl>
  </details>
}

export function OperationsIssuesBrowser({
  adminId, category, onClose,
}: {
  adminId: string
  category: OperationsIssueCategory
  onClose: () => void
}) {
  const [page, setPage] = useState<OperationsIssuePage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [offset, setOffset] = useState(0)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    void getOperationsIssues(adminId, category, offset, 20, search).then((result) => {
      if (active) setPage(result)
    }).catch((cause) => {
      if (active) setError(errorMessage(cause, '获取异常记录失败，请检查管理员权限。'))
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [adminId, category, offset, search])

  return <section className="ops-issue-browser" aria-label={descriptions[category].title}>
    <div className="ops-issue-browser-head">
      <div><strong>{descriptions[category].title} · 全平台只读明细</strong><p>{descriptions[category].summary}</p></div>
      <button type="button" className="icon-button" title="关闭明细" aria-label="关闭明细" onClick={onClose}><X size={17} /></button>
    </div>
    <form className="ops-issue-search" onSubmit={(event) => {
      event.preventDefault()
      setExpanded(null)
      setOffset(0)
      if (search === searchInput.trim() && offset === 0) return
      setLoading(true)
      setError('')
      setSearch(searchInput)
    }}>
      <label htmlFor="ops-issue-query">品牌、账号或任务名</label>
      <input id="ops-issue-query" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} maxLength={80} placeholder="搜索全部账号的异常记录" />
      <button type="submit" className="secondary-button"><Search size={15} />搜索</button>
      {search ? <button type="button" className="text-button" onClick={() => {
        setLoading(true)
        setError('')
        setSearchInput('')
        setSearch('')
        setOffset(0)
      }}>清空</button> : null}
    </form>
    {error ? <p className="service-alert" role="alert">{error}</p> : null}
    <div className="ops-issue-list" aria-live="polite">
      {loading ? <p className="ops-muted"><LoaderCircle size={15} className="spin" /> 正在加载异常记录…</p> : page && page.items.length === 0 ? <p className="ops-muted">没有符合条件的异常记录。</p> : null}
      {!loading && page?.items.map((item) => <article className="ops-issue-entry" key={item.reservationId ?? item.attemptId}>
        <button type="button" className="ops-issue-entry-main" aria-expanded={expanded === (item.reservationId ?? item.attemptId)} onClick={() => setExpanded((old) => old === (item.reservationId ?? item.attemptId) ? null : item.reservationId ?? item.attemptId)}>
          <span className="ops-issue-entry-title"><strong>{item.title}</strong><small>{item.brandName || '未归属品牌'} · {item.username} · {dateText(category === 'reservationReview' ? item.reservationCreatedAt : item.updatedAt)}</small></span>
          <span className="ops-issue-entry-status">{issueAdvice(category, item).urgency === 'now' ? '需要人工核实' : issueAdvice(category, item).urgency === 'later' ? '安排账务核对' : '历史记录 · 按需处理'}</span>
          <ChevronDown size={16} className={expanded === (item.reservationId ?? item.attemptId) ? 'is-open' : ''} />
        </button>
        {expanded === (item.reservationId ?? item.attemptId) ? <div className="ops-human-advice">
          <strong>{issueAdvice(category, item).title}</strong>
          <p>{issueAdvice(category, item).meaning}</p>
          <p><b>建议怎么做：</b>{issueAdvice(category, item).nextStep}</p>
          <IssueDetail item={item} />
        </div> : null}
      </article>)}
    </div>
    {page ? <footer className="ops-issue-pager">
      <span>共 {page.total} 条记录{search ? ' · 搜索结果' : ''} · 第 {page.total ? Math.floor(page.offset / page.limit) + 1 : 0} 页</span>
      <div>
        <button type="button" className="secondary-button" disabled={loading || offset === 0} onClick={() => { setLoading(true); setError(''); setExpanded(null); setOffset(Math.max(0, offset - 20)) }}><ChevronLeft size={15} />上一页</button>
        <button type="button" className="secondary-button" disabled={loading || offset + 20 >= page.total} onClick={() => { setLoading(true); setError(''); setExpanded(null); setOffset(offset + 20) }}>下一页<ChevronRight size={15} /></button>
      </div>
    </footer> : null}
    <p className="ops-issue-readonly">管理员只读诊断入口。不会修改任务、费用、账号权限或发起新的付费生成。</p>
  </section>
}
