import { useEffect, useState } from 'react'
import { ChevronRight, RefreshCw } from 'lucide-react'
import { getAccountEvents } from '../../api'
import type { AccountBrand, AccountEventReport, AdminUser } from '../../types'
import { errorMessage } from '../../errorPresentation'
import { formatTime } from '../../domain/taskPresentation'
import { shanghaiToday, usageDateRange, type UsagePeriod } from './usagePeriod'
import { ReportPagination } from './ReportPagination'
import './accountUsage.css'

const accountActions: Record<string, string> = { LOGIN: '登录', LOGOUT: '退出登录', CREATE_USER: '创建账号', CREATE_BRAND: '创建品牌', UPDATE_ACCOUNT_POLICY: '调整权限或预算', RESET_USER_PASSWORD: '重置密码', DEACTIVATE_USER: '停用账号', CHANGE_PASSWORD: '修改密码' }

export function AccountEventsPanel({ actorId, users, brands }: { actorId: string; users: AdminUser[]; brands: AccountBrand[] }) {
  const [userId, setUserId] = useState('')
  const [brandId, setBrandId] = useState('')
  const [action, setAction] = useState('')
  const [period, setPeriod] = useState<UsagePeriod>('week')
  const [today] = useState(shanghaiToday)
  const [customStart, setCustomStart] = useState(today)
  const [customEnd, setCustomEnd] = useState(today)
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState(20)
  const [refresh, setRefresh] = useState(0)
  const [result, setResult] = useState<{ key: string; report?: AccountEventReport; error?: string } | null>(null)
  const { startDate, endDate } = usageDateRange(period, today, customStart, customEnd)
  const invalid = !startDate || !endDate || startDate > endDate
  const key = JSON.stringify([actorId, userId, brandId, action, startDate, endDate, offset, limit, refresh])
  const busy = !invalid && result?.key !== key
  const report = result?.key === key ? result.report : undefined
  const error = result?.key === key ? result.error : ''
  useEffect(() => {
    if (invalid) return
    let active = true
    getAccountEvents({ userId, brandId, action, startDate, endDate, offset, limit }, actorId)
      .then((report) => { if (active) setResult({ key, report }) })
      .catch((cause) => { if (active) setResult({ key, error: errorMessage(cause, '操作记录加载失败') }) })
    return () => { active = false }
  }, [actorId, userId, brandId, action, startDate, endDate, offset, limit, key, invalid])
  const filter = (change: () => void) => { change(); setOffset(0) }
  return <section className="settings-section account-events-section" aria-label="账号操作记录">
    <div className="settings-section-heading"><div><h2>操作记录</h2><p>北京时间</p></div><button type="button" className="secondary-button" disabled={busy || invalid} onClick={() => setRefresh((v) => v + 1)}><RefreshCw size={16} />刷新记录</button></div>
    <div className="account-policy-fields usage-filters">
      <label>品牌<select aria-label="品牌" value={brandId} onChange={(e) => filter(() => { setBrandId(e.target.value); setUserId('') })}><option value="">所有可访问品牌</option>{brands.map((b) => <option value={b.id} key={b.id}>{b.displayName ?? b.name}</option>)}</select></label>
      <label>操作账号<select aria-label="操作账号" value={userId} onChange={(e) => filter(() => setUserId(e.target.value))}><option value="">所有可访问账号</option>{users.filter((u) => !brandId || u.brandId === brandId).map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}</select></label>
      <label>操作类型<select aria-label="操作类型" value={action} onChange={(e) => filter(() => setAction(e.target.value))}><option value="">全部类型</option>{Object.entries(accountActions).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
      <label>统计范围<select aria-label="统计范围" value={period} onChange={(e) => filter(() => setPeriod(e.target.value as UsagePeriod))}><option value="today">今天</option><option value="week">本周</option><option value="month">本月</option><option value="custom">自定义区间</option></select></label>
      <label>开始日期<input type="date" max="9999-12-30" value={startDate} readOnly={period !== 'custom'} onChange={(e) => filter(() => setCustomStart(e.target.value))} /></label>
      <label>结束日期<input type="date" max="9999-12-30" value={endDate} readOnly={period !== 'custom'} onChange={(e) => filter(() => setCustomEnd(e.target.value))} /></label>
    </div>
    {invalid || error ? <div role="alert" className="inline-alert inline-alert--danger">{invalid ? '请选择完整的日期范围，开始日期不能晚于结束日期。' : error}</div> : null}
    {busy ? <p role="status">正在读取操作记录…</p> : null}
    {report && !invalid ? <>
      <div className="usage-table-scroll" role="region" aria-label="操作记录表" tabIndex={0}><table className="usage-table"><thead><tr><th scope="col">时间</th><th scope="col">操作账号</th><th scope="col">操作</th><th scope="col">详情</th></tr></thead>
        <tbody>{report.items.map((event) => <tr key={event.id}><td>{formatTime(event.createdAt)}</td><td>{event.username}</td><td>{accountActions[event.action] ?? '账号操作'}</td><td><details><summary><ChevronRight size={14} />查看详情</summary><dl className="event-detail"><dt>操作类型</dt><dd>{event.action}</dd><dt>对象类型</dt><dd>{event.entityType === 'user' ? '账号' : event.entityType === 'brand' ? '品牌' : event.entityType}</dd><dt>对象编号</dt><dd>{event.entityId}</dd></dl></details></td></tr>)}</tbody></table></div>
      {!report.items.length ? <p className="account-empty">所选条件下没有操作记录。</p> : null}
      <ReportPagination label="操作记录" offset={offset} limit={limit} total={report.total} onPage={setOffset} onLimit={(value) => filter(() => setLimit(value))} />
    </> : null}
  </section>
}
