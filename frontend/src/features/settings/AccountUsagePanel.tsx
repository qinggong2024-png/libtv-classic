import { errorMessage } from '../../errorPresentation'
import { ArrowLeft, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'
import { Fragment, useEffect, useRef, useState } from 'react'
import { getAccountUsage } from '../../api'
import { formatTime } from '../../domain/taskPresentation'
import type { AccountBrand, AccountUsageReport, AdminUser } from '../../types'
import { shanghaiToday, usageDateRange, type UsagePeriod } from './usagePeriod'
import './accountUsage.css'
import { ReportPagination } from './ReportPagination'
import { usagePresentation } from './usagePresentation'

const dollars = (amount: number) => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'USD', maximumFractionDigits: 6 }).format(amount)
const statuses: Record<string, string> = { RESERVED: '已预留', ESTIMATED: '已结束 · 估算', UNCERTAIN: '结果待核对', RELEASED: '已释放' }
const actions: Record<string, string> = { LOGIN: '登录', LOGOUT: '退出登录', CREATE_USER: '创建账号', CREATE_BRAND: '创建品牌', UPDATE_ACCOUNT_POLICY: '调整权限或预算', RESET_USER_PASSWORD: '重置密码', DEACTIVATE_USER: '停用账号', CHANGE_PASSWORD: '修改密码' }

export function AccountUsagePanel({ actorId, users = [], brands = [], personal = false }: { actorId?: string; users?: AdminUser[]; brands?: AccountBrand[]; personal?: boolean }) {
  const [userId, setUserId] = useState('')
  const [brandId, setBrandId] = useState('')
  const [today, setToday] = useState(shanghaiToday)
  const [period, setPeriod] = useState<UsagePeriod>('today')
  const [customStart, setCustomStart] = useState(today)
  const [customEnd, setCustomEnd] = useState(today)
  const [status, setStatus] = useState('')
  const [offset, setOffset] = useState(0)
  const [summaryOffset, setSummaryOffset] = useState(0)
  const [eventOffset, setEventOffset] = useState(0)
  const [eventPageSize, setEventPageSize] = useState(20)
  const [pageSize, setPageSize] = useState(20)
  const [details, setDetails] = useState(personal)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [report, setReport] = useState<AccountUsageReport | null>(null)
  const [reportActor, setReportActor] = useState<string | undefined>(actorId)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const detailHeading = useRef<HTMLHeadingElement>(null)
  const focusDetail = useRef(false)
  const { startDate, endDate } = usageDateRange(period, today, customStart, customEnd)
  const invalidRange = !startDate || !endDate || startDate > endDate
  const policyRevision = users.map((user) => `${user.id}:${user.dailyBudgetUsd}:${user.isActive}`).join('|')
  useEffect(() => {
    let active = true
    if (invalidRange) return
    getAccountUsage({ userId: personal ? actorId : userId, brandId, startDate, endDate, status, offset, limit: pageSize, summaryOffset, summaryLimit: pageSize, eventOffset, eventLimit: eventPageSize, includeEvents: personal }, actorId).then((data) => {
      if (active) {
        setReport(data); setReportActor(actorId); setError(''); setBusy(false)
        if (data.budgetDay) setToday(data.budgetDay)
      }
    }).catch((cause) => { if (active) { setError(errorMessage(cause, '记录加载失败')); setBusy(false) } })
    return () => { active = false }
  }, [actorId, userId, brandId, startDate, endDate, status, offset, summaryOffset, eventOffset, eventPageSize, pageSize, refreshKey, personal, policyRevision, invalidRange])
  useEffect(() => {
    if (!busy && report && focusDetail.current) {
      detailHeading.current?.focus({ preventScroll: true })
      detailHeading.current?.scrollIntoView({ block: 'start', behavior: 'instant' })
      focusDetail.current = false
    }
  }, [busy, report])
  const filter = (change: () => void) => {
    change(); setOffset(0); setSummaryOffset(0); setEventOffset(0); setEventPageSize(20); setExpanded(null); setBusy(true); setReport(null); setError('')
    setRefreshKey((value) => value + 1)
  }
  const drill = (id: string, nextStatus = '') => {
    focusDetail.current = true
    filter(() => { setUserId(id); setStatus(nextStatus); setDetails(true) })
  }
  return <section className="settings-section account-usage-section" aria-label="账号使用记录">
    <div className="settings-section-heading"><div><h2>使用记录与美元预算</h2><p>北京时间 · USD 估算费用，实际扣费以供应商账单为准。</p></div><button className="secondary-button" type="button" disabled={busy && !invalidRange} onClick={() => { setBusy(true); setRefreshKey((v) => v + 1) }}><RefreshCw size={16} />刷新记录</button></div>
    <div className="account-policy-fields usage-filters">
      {brands.length ? <label>品牌<select aria-label="品牌" value={brandId} onChange={(e) => filter(() => { setBrandId(e.target.value); setUserId(''); setStatus('') })}><option value="">所有可访问品牌</option>{brands.map((b) => <option key={b.id} value={b.id}>{b.displayName ?? b.name}</option>)}</select></label> : null}
      {users.length ? <label>账号<select aria-label="账号" value={userId} onChange={(e) => filter(() => { setUserId(e.target.value); setStatus('') })}><option value="">所有可访问账号</option>{users.filter((u) => !brandId || u.brandId === brandId).map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}</select></label> : null}
      <label className="usage-period-filter">统计范围<select aria-label="统计范围" value={period} onChange={(e) => filter(() => { setPeriod(e.target.value as UsagePeriod); setStatus('') })}><option value="today">今天</option><option value="week">本周</option><option value="month">本月</option><option value="custom">自定义区间</option></select></label>
      <label>开始日期<input type="date" value={startDate} readOnly={period !== 'custom'} max="9999-12-30" onChange={(e) => filter(() => setCustomStart(e.target.value))} /></label>
      <label>结束日期<input type="date" value={endDate} readOnly={period !== 'custom'} max="9999-12-30" onChange={(e) => filter(() => setCustomEnd(e.target.value))} /></label>
    </div>
    {invalidRange || error ? <div className="inline-alert inline-alert--danger" role="alert">{invalidRange ? '请选择完整的日期范围，开始日期不能晚于结束日期。' : error}</div> : null}
    {!personal ? <div className="usage-view-switch" role="group" aria-label="费用视图"><button type="button" aria-pressed={!details} onClick={() => filter(() => { setDetails(false); setUserId(''); setStatus('') })}>账号汇总</button><button type="button" aria-pressed={details} onClick={() => filter(() => setDetails(true))}>用量明细</button></div> : null}
    {!invalidRange && (busy || reportActor !== actorId ? <p role="status">正在读取使用记录…</p> : report && !error ? <>
      <p>{report.startDate ?? report.day} 至 {report.endDate ?? report.day} · {report.recordCount ?? report.total} 条用量记录（含生成与剪辑）</p>
      <dl className="usage-totals">{([
        ['区间预算占用', report.budgetUsedUsd], ['已结束估算', report.estimatedUsd],
        ['已预留', report.reservedUsd], ['结果待核对', report.uncertainUsd], ['已释放', report.releasedUsd ?? 0],
      ] as const).map(([label, value]) => <div key={label}><dt>{label}{label === '区间预算占用' && report.unpricedCount ? '（已知）' : ''}</dt><dd>{dollars(value)}</dd></div>)}</dl>
      {report.unpricedCount ? <p role="status">区间内有 {report.unpricedCount} 条未定价记录，金额未计入合计。今日存在未定价记录时，已设置预算的账号会暂停新生成。</p> : null}
      <p>图片按张、视频生成按秒统计；剪辑按送剪原片条数统计，详见用量明细。送剪原片数不等于剪辑任务数或回传成片数。</p>
      <p>用量按记录统计，包含已预留、已结束、结果待核对与已释放记录，不代表成功产出数。</p>
      <p>区间预算占用包含已结束预估、进行中预留和结果待核对的预估金额，待核对部分不是额外收费，也不代表已实际扣款；已释放不计入。历史未定价费用不补算，分析服务费用暂不计入。</p>
      {!personal && !details && report.summaries ? <>
        <div className="usage-overview-heading"><h3>账号金额汇总</h3></div>
        <p>每日额度日期：{report.budgetDay}（北京时间），与上方统计区间分别计算。</p>
        <div className="usage-table-scroll" role="region" aria-label="账号金额汇总表" tabIndex={0}><table className="usage-table usage-account-table">
          <caption className="sr-only">账号金额汇总</caption>
          <thead><tr>{['账号 / 品牌', '图片／视频生成用量', '区间占用', '结果待核对', '今日额度 / 剩余', '金额拆分'].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
          <tbody>{report.summaries.map((summary) => <Fragment key={summary.userId}><tr>
            <th scope="row"><button className="usage-record-link" type="button" onClick={() => drill(summary.userId)} aria-label={`查看 ${summary.username} 的全部用量明细`}>{summary.username}</button><small>{brands.find((brand) => brand.id === summary.brandId)?.displayName ?? brands.find((brand) => brand.id === summary.brandId)?.name ?? '未分配品牌'}{summary.isActive ? '' : ' · 已停用'}</small></th>
            <td>图片 {summary.imageCount} 张 / 视频 {summary.videoSeconds} 秒<small>{summary.recordCount} 条用量记录（含剪辑）{summary.unknownVideoCount ? ` · ${summary.unknownVideoCount} 条视频时长未记录` : ''}</small></td>
            <td><button className="usage-record-link" type="button" onClick={() => drill(summary.userId, 'OCCUPIED')} aria-label={`查看 ${summary.username} 的区间占用明细`}>{dollars(summary.budgetUsedUsd)}</button>{summary.unpricedCount ? <small>已知金额 · {summary.unpricedCount} 笔未定价</small> : null}</td>
            <td><button className="usage-record-link" type="button" onClick={() => drill(summary.userId, 'UNCERTAIN')} aria-label={`查看 ${summary.username} 的结果待核对明细`}>{dollars(summary.uncertainUsd)}</button></td>
            <td><span>{summary.todayUnpricedCount ? '已知占用 ' : '已占用 '}{dollars(summary.todayBudgetUsedUsd)}</span><small>每日预算 {summary.dailyBudgetUsd === null ? '未设置' : dollars(summary.dailyBudgetUsd)}</small><small>{summary.dailyBudgetUsd === null ? '未设限额' : summary.todayUnpricedCount ? `剩余待核实 · ${summary.todayUnpricedCount} 笔未定价` : summary.todayRemainingUsd === null ? '剩余待核实' : `剩余 ${dollars(summary.todayRemainingUsd)}`}</small></td>
            <td><button className="icon-button" type="button" aria-label={`${expanded === summary.userId ? '收起' : '展开'} ${summary.username} 的金额拆分`} title="金额拆分" aria-expanded={expanded === summary.userId} aria-controls={`usage-breakdown-${summary.userId}`} onClick={() => setExpanded((id) => id === summary.userId ? null : summary.userId)}>{expanded === summary.userId ? <ChevronDown size={18} /> : <ChevronRight size={18} />}</button></td>
          </tr>{expanded === summary.userId ? <tr className="usage-breakdown-row"><td colSpan={6}><dl className="usage-breakdown" id={`usage-breakdown-${summary.userId}`}>{([
            ['已结束估算', summary.estimatedUsd, 'ESTIMATED'], ['已预留', summary.reservedUsd, 'RESERVED'], ['已释放', summary.releasedUsd, 'RELEASED'],
          ] as const).map(([label, amount, feeStatus]) => <div key={label}><dt>{label}</dt><dd><button className="usage-record-link" type="button" onClick={() => drill(summary.userId, feeStatus)} aria-label={`查看 ${summary.username} 的${label}明细`}>{dollars(amount)}</button></dd></div>)}<div><dt>未定价</dt><dd>{summary.unpricedCount} 笔</dd></div></dl></td></tr> : null}</Fragment>)}</tbody>
        </table>{!report.summaries.length ? <p>没有可访问的账号。</p> : null}</div>
        <ReportPagination label="账号汇总" offset={summaryOffset} limit={pageSize} total={report.summaryTotal ?? report.summaries.length} onPage={(value) => { setBusy(true); setExpanded(null); setSummaryOffset(value) }} onLimit={(value) => filter(() => setPageSize(value))} />
      </> : null}
      {personal && report.summaries?.[0] ? <p>今日预算：{report.summaries[0].dailyBudgetUsd === null ? '未设置' : dollars(report.summaries[0].dailyBudgetUsd)} · 今日已知占用 {dollars(report.summaries[0].todayBudgetUsedUsd)}</p> : null}
      {details ? <>
      {!personal ? <button className="secondary-button" type="button" onClick={() => filter(() => { setDetails(false); setUserId(''); setStatus('') })}><ArrowLeft size={16} />返回账号汇总</button> : null}
      <div className="usage-detail-heading"><h3 ref={detailHeading} tabIndex={-1}>用量明细{userId ? ` · ${users.find((user) => user.id === userId)?.username ?? ''}` : ''}</h3><label>费用状态<select aria-label="费用状态" value={status} onChange={(e) => filter(() => setStatus(e.target.value))}><option value="">全部状态</option><option value="OCCUPIED">计入占用</option>{Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
      <div className="usage-table-scroll" role="region" aria-label="用量明细表" tabIndex={0}><table className="usage-table"><caption>图片生成、视频生成与剪辑用量记录</caption><thead><tr><th scope="col">时间</th><th scope="col">账号</th><th scope="col">用途</th><th scope="col">数量</th><th scope="col">预估 USD</th><th scope="col">状态</th></tr></thead><tbody>{report.items.map((item) => <tr key={item.id}><td>{formatTime(item.createdAt)}</td><td>{item.username}</td><td>{usagePresentation(item).label}</td><td>{usagePresentation(item).quantity}</td><td>{item.amountUsd === null ? '未定价' : dollars(item.amountUsd)}</td><td>{statuses[item.status] ?? '结果待核对'}</td></tr>)}</tbody></table>{!report.items.length ? <p>所选区间及状态没有用量记录。</p> : null}</div>
      <ReportPagination label="用量明细" offset={offset} limit={pageSize} total={report.total} onPage={(value) => { setBusy(true); setOffset(value) }} onLimit={(value) => filter(() => setPageSize(value))} />
      </> : null}
      {personal ? <><h3>账号操作记录</h3><div className="usage-event-list">{report.events.length ? report.events.map((e) => <p key={e.id}><time>{formatTime(e.createdAt)}</time><strong>{e.username}</strong><span>{actions[e.action] ?? '账号操作'}</span></p>) : <p>暂无账号操作记录。</p>}</div><ReportPagination label="账号操作" offset={eventOffset} limit={eventPageSize} total={report.eventTotal} onPage={(value) => { setBusy(true); setEventOffset(value) }} onLimit={(value) => { setBusy(true); setEventOffset(0); setEventPageSize(value) }} /></> : null}
    </> : null)}
  </section>
}
