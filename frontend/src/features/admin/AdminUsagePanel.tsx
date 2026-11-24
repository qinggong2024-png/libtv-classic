import { AlertTriangle, ChevronRight, Info, RefreshCw, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getAdminAccountUsage, type AdminUsageQuery } from '../../api'
import { formatTime } from '../../domain/taskPresentation'
import { errorMessage } from '../../errorPresentation'
import { isManagementAdmin } from '../../pageAccess'
import { EmptyState } from '../../shared/components/Section'
import { FoundationDrawer } from '../../shared/components/ui'
import type { AccountBrand, AccountUsageReport, AdminUser, AuthUser } from '../../types'
import { selectableBrands } from './brandOptions'
import { ReportPagination } from '../settings/ReportPagination'
import { shanghaiToday, usageDateRange, type UsagePeriod } from '../settings/usagePeriod'
import { usagePresentation } from '../settings/usagePresentation'
import { UsageDateRangePicker } from './UsageDateRangePicker'
import '../settings/accountUsage.css'
import './adminUsage.css'

const dollars = (amount: number) => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'USD', maximumFractionDigits: 6 }).format(amount)
const statuses: Record<string, string> = { ESTIMATED: '已结束 · 预估', RESERVED: '进行中预估', UNCERTAIN: '结果待核对', RELEASED: '已释放' }
const periods: { value: UsagePeriod; label: string }[] = [{ value: 'today', label: '今天' }, { value: 'week', label: '本周' }, { value: 'month', label: '本月' }, { value: 'custom', label: '自定义' }]
type Selection = { userId?: string; username?: string; status: string }

// Results are bound to the complete query and actor. Previous requests cannot reveal
// stale rows after an account, date, brand or permission change.
function useUsageReport(query: AdminUsageQuery, actorId: string, revision: string, enabled = true) {
  const queryKey = JSON.stringify(query)
  const key = JSON.stringify([actorId, queryKey, revision])
  const [result, setResult] = useState<{ key: string; report?: AccountUsageReport; error?: string }>()
  useEffect(() => {
    if (!enabled) return
    let active = true
    getAdminAccountUsage(JSON.parse(queryKey) as AdminUsageQuery, actorId).then((report) => {
      if (active) setResult({ key, report })
    }).catch((cause) => {
      if (active) setResult({ key, error: errorMessage(cause, '用量记录加载失败，请刷新重试。') })
    })
    return () => { active = false }
  }, [actorId, queryKey, revision, key, enabled])
  return enabled && result?.key === key ? { ...result, busy: false } : { busy: enabled, report: undefined, error: undefined }
}

export function AdminUsagePanel(props: { actor: AuthUser; users: AdminUser[]; brands: AccountBrand[] }) {
  if (!isManagementAdmin(props.actor)) return <EmptyState icon={<Info size={24} />} title="没有管理中心权限" description="仅已授权的平台管理员可以查看全站用量与费用。" />
  return <AdminUsageWorkspace key={props.actor.userId} {...props} />
}

function AdminUsageWorkspace({ actor, users, brands }: { actor: AuthUser; users: AdminUser[]; brands: AccountBrand[] }) {
  const [today, setToday] = useState(shanghaiToday)
  const [period, setPeriod] = useState<UsagePeriod>('today')
  const [customStart, setCustomStart] = useState(today)
  const [customEnd, setCustomEnd] = useState(today)
  const [brandId, setBrandId] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState(20)
  const [refresh, setRefresh] = useState(0)
  const [selection, setSelection] = useState<Selection | null>(null)
  const { startDate, endDate } = usageDateRange(period, today, customStart, customEnd)
  const invalidRange = !startDate || !endDate || startDate > endDate
  const revision = JSON.stringify([refresh, users.map((user) => [user.id, user.dailyBudgetUsd, user.isActive])])
  const { report, busy, error } = useUsageReport({ brandId, search: search.trim(), startDate, endDate, summaryOffset: offset, summaryLimit: limit, limit: 1, includeEvents: false }, actor.userId, revision, !invalidRange)
  // The existing API reports today's quota independently from the filtered usage.
  // A historical day uses that day's occupied estimate and the current configured
  // daily limit; historical limit values are not recorded by the policy audit.
  const historical = endDate < today
  const multiDay = startDate !== endDate
  const endDayReport = useUsageReport({ brandId, search: search.trim(), startDate: endDate, endDate, summaryOffset: offset, summaryLimit: limit, limit: 1, includeEvents: false }, actor.userId, revision, !invalidRange && historical && multiDay)
  const quotaLabel = multiDay ? (historical ? '截止日剩余额度（估算）' : '截止日剩余额度') : historical ? '当日剩余额度（估算）' : '今日剩余额度'
  const brandName = brands.find((brand) => brand.id === brandId)?.displayName ?? brands.find((brand) => brand.id === brandId)?.name ?? '全部品牌'
  const change = (update: () => void) => { update(); setOffset(0); setSelection(null) }
  const open = (status = '', userId?: string, username?: string) => setSelection({ status, userId, username })
  return <section className="settings-section account-usage-section admin-usage" aria-label="账号使用记录">
    <div className="admin-usage-toolbar">
      <div className="admin-usage-periods" data-period={period} role="group" aria-label="统计范围">{periods.map((item) => <button type="button" key={item.value} aria-pressed={period === item.value} onClick={() => change(() => setPeriod(item.value))}>{item.label}</button>)}</div>
      {period === 'custom' ? <UsageDateRangePicker start={customStart} end={customEnd} today={today} onChange={(start, end) => change(() => { setCustomStart(start); setCustomEnd(end) })} /> : null}
      <label className="admin-usage-brand"><span className="sr-only">品牌</span><select aria-label="品牌" value={brandId} onChange={(event) => change(() => setBrandId(event.target.value))}><option value="">全部品牌</option>{selectableBrands(brands).map((brand) => <option key={brand.id} value={brand.id}>{brand.displayName ?? brand.name}</option>)}</select></label>
      <label className="admin-usage-search"><Search size={16} aria-hidden="true" /><span className="sr-only">搜索账号</span><input type="search" value={search} maxLength={80} placeholder="搜索账号" onChange={(event) => change(() => setSearch(event.target.value))} /></label>
      <button className="secondary-button admin-usage-refresh" type="button" disabled={busy} onClick={() => { setToday(shanghaiToday()); setRefresh((value) => value + 1) }}><RefreshCw size={16} aria-hidden="true" />刷新记录</button>
    </div>
    {invalidRange ? <div className="inline-alert inline-alert--danger" role="alert">请选择完整的日期范围，开始日期不能晚于结束日期。</div> : <>
      <div className="admin-usage-meta"><p className="admin-usage-range">{startDate === endDate ? startDate : startDate + ' 至 ' + endDate}{report ? ' · ' + (report.summaryTotal ?? report.summaries.length) + ' 个账号 · ' + report.recordCount + ' 条记录' : ''}</p><span className="admin-usage-timezone">北京时间 · USD</span></div>
      {error ? <div className="inline-alert inline-alert--danger" role="alert">{error}</div> : busy ? <div className="admin-usage-loading" role="status"><span className="sr-only">正在读取用量与费用…</span><div className="admin-usage-skeleton-metrics" aria-hidden="true">{[0, 1, 2, 3].map(i => <div key={i}><i /><i /></div>)}</div><div className="admin-usage-skeleton-table" aria-hidden="true">{[0, 1, 2, 3, 4].map(i => <div key={i}><i /><i /><i /><i /></div>)}</div></div> : report ? <div className="admin-usage-results">
        <div className="admin-usage-metrics">
        <div className="admin-usage-money-row">
          <dl className="admin-usage-totals"><div><dt>费用预估合计</dt><dd>{dollars(report.budgetUsedUsd)}</dd></div></dl>
          <span className="admin-usage-estimate-hint">预估 · 非实际扣费</span>
        </div>
        <dl className="admin-usage-cost-breakdown" aria-label="费用预估组成">
          {([
            ['已结束任务', report.estimatedUsd, 'ESTIMATED', '已结束任务预估'],
            ['进行中任务', report.reservedUsd, 'RESERVED', '进行中任务预估'],
            ['待核对 · 已计入', report.uncertainUsd, 'UNCERTAIN', '待核对预估 · 已计入合计'],
          ] as const).map(([label, amount, status, ariaLabel]) => <div key={status} data-attention={status === 'UNCERTAIN' && amount > 0 ? 'true' : undefined}>
            <dt>{label}</dt><dd><button type="button" className="admin-usage-text-button" aria-label={`查看${ariaLabel}明细`} onClick={() => open(status)}>{dollars(amount)}<ChevronRight size={15} aria-hidden="true" /></button></dd>
          </div>)}
        </dl>
        </div>
        {report.releasedUsd > 0 ? <p className="admin-usage-explainer">已释放 <button type="button" className="admin-usage-text-button" onClick={() => open('RELEASED')}>{dollars(report.releasedUsd)}<ChevronRight size={13} aria-hidden="true" /></button> · 不计入合计</p> : null}
        {report.unpricedCount > 0 ? <div className="admin-usage-warning" role="status"><AlertTriangle size={17} aria-hidden="true" /><span><strong>费用待核实：{report.unpricedCount} 条</strong>缺少定价，暂未计入金额。</span><button className="admin-usage-text-button" type="button" onClick={() => open('UNPRICED')}>查看记录<ChevronRight size={16} aria-hidden="true" /></button></div> : null}
        <div className="usage-overview-heading"><h3>账号用量</h3><button type="button" className="admin-usage-text-button" onClick={() => open()}>查看全部明细<ChevronRight size={16} aria-hidden="true" /></button></div>
        <div className="usage-table-scroll" role="region" aria-label="账号用量汇总表" tabIndex={0}><table className="usage-table usage-account-table admin-usage-table"><caption className="sr-only">账号用量汇总，包含生成用量、原片及 CUT 成片下载次数</caption><thead><tr>{['账号 / 品牌', '图片（张）', '视频（秒）', '送剪原片（条）', '已下载原片（条）', '已下载 CUT 成片（条）', '预估费用', quotaLabel, '明细'].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{report.summaries.map((row) => {
          const quotaRow = historical && multiDay ? endDayReport.report?.summaries.find((item) => item.userId === row.userId) : row
          const limit = quotaRow?.dailyBudgetUsd
          const liveQuota = !historical && report.budgetDay === endDate
          const remaining = quotaRow && limit != null && quotaRow.unpricedCount === 0
            ? Math.max(0, Math.round(limit * 1e6) - Math.round(quotaRow.budgetUsedUsd * 1e6)) / 1e6 : null
          const quota = liveQuota ? row.todayRemainingUsd : historical ? remaining : null
          const unpriced = liveQuota ? row.todayUnpricedCount : quotaRow?.unpricedCount
          return <tr key={row.userId}>
          <th scope="row"><button type="button" className="usage-record-link" onClick={() => open('', row.userId, row.username)} aria-label={'查看 ' + row.username + ' 的用量明细'}>{row.username}</button><small>{brands.find((brand) => brand.id === row.brandId)?.displayName ?? brands.find((brand) => brand.id === row.brandId)?.name ?? '未分配品牌'}{row.isActive ? '' : ' · 已停用'}</small></th>
          <td>{row.imageCount}</td><td>{row.videoSeconds}{row.unknownVideoCount > 0 ? <small>{row.unknownVideoCount} 条时长未记录</small> : null}</td><td>{row.cutflowClips ?? '—'}</td><td title={row.originalDownloads === undefined ? '下载统计尚未启用' : undefined}>{row.originalDownloads ?? '—'}</td><td title={row.cutflowDownloads === undefined ? '下载统计尚未启用' : undefined}>{row.cutflowDownloads ?? '—'}</td>
          <td><button type="button" className="usage-record-link" aria-label={'查看 ' + row.username + ' 的预估费用明细'} onClick={() => open('OCCUPIED', row.userId, row.username)}>{dollars(row.budgetUsedUsd)}</button>{row.unpricedCount > 0 ? <small className="admin-usage-caution">另有 {row.unpricedCount} 条待核实</small> : null}{row.uncertainUsd > 0 ? <small>含待核对预估 {dollars(row.uncertainUsd)}</small> : null}</td>
          <td>{!quotaRow ? <span>{endDayReport.error ? '读取失败' : endDayReport.busy ? '读取中…' : '—'}</span> : limit === null ? <span>未设限额</span> : unpriced || quota == null ? <span className="admin-usage-caution">剩余待核实</span> : <strong>{dollars(quota)}</strong>}<small>{historical ? '现行每日额度' : '每日额度'} {limit == null ? (limit === null ? '未设置' : '—') : dollars(limit)}</small></td>
          <td><button type="button" className="icon-button" aria-label={'打开 ' + row.username + ' 的明细'} onClick={() => open('', row.userId, row.username)}><ChevronRight size={18} /></button></td>
        </tr>})}</tbody></table>{report.summaries.length === 0 ? <p className="account-empty">{search || brandId ? '没有符合筛选条件的账号。' : '暂无账号用量。'}</p> : null}</div>
        <div className="admin-usage-footer"><span>{multiDay ? `额度对应截止日 ${endDate}。` : ''}{historical ? ' 历史额度按现行限额估算，非当日留存快照。' : ''}</span><ReportPagination label="账号用量" offset={offset} limit={limit} total={report.summaryTotal ?? report.summaries.length} onPage={setOffset} onLimit={(value) => change(() => setLimit(value))} /></div>
        <details className="admin-usage-definitions"><summary><Info size={14} aria-hidden="true" />统计口径</summary><ul><li>待核对预估不是额外收费，不代表已实际扣款；以供应商账单为准。</li><li>图片按张、视频生成按秒、剪辑按送剪原片条数统计；送剪原片数不等于剪辑任务数或回传成片数。</li><li>原片和 CUT 成片分别按账号统计首次请求下载的唯一文件数；同一文件重复下载不计，归属首次下载日期，预览不计入。仅记录功能上线后的下载请求；无法确认浏览器是否最终保存文件。</li><li>用量包含进行中、已结束、结果待核对和已释放记录，不代表成功产出数。</li><li>预估费用包含已结束预估、进行中预估和结果待核对，不含已释放金额；历史未定价费用不补算，分析服务费用暂不计入。</li><li>今日有未定价记录时，已设置每日预算的账号会暂停新生成，剩余额度显示待核实。</li></ul></details>
      </div> : null}
    </>}
    <FoundationDrawer open={selection !== null} title={selection?.username ? selection.username + ' 的用量明细' : selection?.status === 'UNPRICED' ? '费用待核实记录' : selection?.status === 'UNCERTAIN' ? '结果待核对明细' : '全部用量明细'} onClose={() => setSelection(null)}>
      {selection ? <UsageDrawerContent key={JSON.stringify(selection)} actorId={actor.userId} selection={selection} startDate={startDate} endDate={endDate} brandId={brandId} brandName={brandName} search={search.trim()} revision={revision} onRefresh={() => setRefresh((value) => value + 1)} /> : null}
    </FoundationDrawer>
  </section>
}

function UsageDrawerContent({ actorId, selection, startDate, endDate, brandId, brandName, search, revision, onRefresh }: { actorId: string; selection: Selection; startDate: string; endDate: string; brandId: string; brandName: string; search: string; revision: string; onRefresh: () => void }) {
  const [status, setStatus] = useState(selection.status)
  const [kind, setKind] = useState('')
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState(20)
  const { report, busy, error } = useUsageReport({ userId: selection.userId, brandId, search: selection.userId ? undefined : search, startDate, endDate, status: status === 'UNPRICED' ? undefined : status, unpricedOnly: status === 'UNPRICED', kind, offset, limit, summaryLimit: 1, includeEvents: false }, actorId, revision)
  return <div className="admin-usage-detail">
    <p className="admin-usage-detail-scope">{startDate} 至 {endDate} · {brandName}</p>
    <div className="admin-usage-detail-filters"><label>用途<select aria-label="用途" value={kind} onChange={(event) => { setKind(event.target.value); setOffset(0) }}><option value="">全部用途</option><option value="IMAGE">图片生成</option><option value="VIDEO">视频生成</option><option value="CUTFLOW">视频剪辑</option></select></label><label>费用状态<select aria-label="费用状态" value={status} onChange={(event) => { setStatus(event.target.value); setOffset(0) }}><option value="">全部状态</option><option value="OCCUPIED">计入预估费用</option>{Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}<option value="UNPRICED">费用待核实（未定价）</option></select></label></div>
    {error ? <div className="inline-alert inline-alert--danger" role="alert">{error}<button className="secondary-button" type="button" onClick={onRefresh}>重新加载</button></div> : busy ? <p role="status">正在读取用量明细…</p> : report ? <>
      <div className="admin-usage-detail-total"><span>所选日期预估费用</span><strong>{dollars(report.budgetUsedUsd)}</strong><small>区间总额，不受明细筛选影响</small></div>
      {report.unpricedCount > 0 ? <p className="admin-usage-caution">另有 {report.unpricedCount} 条费用待核实，暂未计入金额。</p> : null}
      <div className="usage-table-scroll" role="region" aria-label="用量明细表" tabIndex={0}><table className="usage-table admin-usage-ledger"><caption className="sr-only">图片生成、视频生成与剪辑用量记录</caption><thead><tr><th scope="col">用途 / 数量</th><th scope="col">预估费用</th><th scope="col">状态</th></tr></thead><tbody>{report.items.map((item) => <tr key={item.id}><th scope="row"><strong>{usagePresentation(item).label}</strong><span>{usagePresentation(item).quantity}</span><small>{formatTime(item.createdAt)}{selection.userId ? '' : ' · ' + item.username}</small></th><td>{item.amountUsd === null ? '待核实' : dollars(item.amountUsd)}</td><td><span className={'admin-usage-status admin-usage-status--' + item.status.toLowerCase()}>{statuses[item.status] ?? '结果待核对'}</span></td></tr>)}</tbody></table>{report.items.length === 0 ? <p className="account-empty">所选区间及筛选条件没有用量记录。</p> : null}</div>
      <ReportPagination label="用量明细" offset={offset} limit={limit} total={report.total} onPage={setOffset} onLimit={(value) => { setLimit(value); setOffset(0) }} />
    </> : null}
  </div>
}
