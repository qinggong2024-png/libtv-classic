import { useEffect, useState } from 'react'
import { ChevronRight, RefreshCw } from 'lucide-react'
import { getAccountEvents } from '../../api'
import type { AccountBrand, AccountEventReport, AdminUser } from '../../types'
import { errorMessage } from '../../errorPresentation'
import { parseTaskTimestamp } from '../../taskTiming'
import { shanghaiToday, usageDateRange, type UsagePeriod } from './usagePeriod'
import { ReportPagination } from './ReportPagination'
import { equivalentBrandIds, selectableBrands } from '../admin/brandOptions'
import { AdminReportPeriod } from '../admin/AdminReportPeriod'
import { FoundationDrawer } from '../../shared/components/ui'
import './accountUsage.css'
import '../admin/adminUsage.css'
import './accountEvents.css'

const accountActions: Record<string, string> = {
  LOGIN: '登录系统', LOGOUT: '退出登录', CREATE_USER: '创建账号', CREATE_BRAND: '创建品牌',
  UPDATE_ACCOUNT_POLICY: '调整权限或预算', RESET_USER_PASSWORD: '重置密码', DEACTIVATE_USER: '停用账号', CHANGE_PASSWORD: '修改密码',
  CREATE_VIDEO_TASK: '创建生视频任务', CREATE_IMAGE_TASK: '创建生图任务', CREATE_CUT_TASK: '创建剪辑任务',
  DOWNLOAD_ORIGINAL_VIDEO: '下载原片', DOWNLOAD_CUTFLOW_VIDEO: '下载 CUT 成片',
  FINAL_VIDEO_APPROVAL: '审核视频', IMAGE_QUALITY_DECISION: '审核图片',
  EDIT_TASK_PROMPTS: '修改视频提示词', REGENERATE_TASK_PROMPTS: '重新编排提示词', CONFIRM_TASK: '确认视频任务',
  BACH_CONFIG_UPDATED: '调整 Bach 配置', WAVESPEED_CONFIG_UPDATED: '调整 WaveSpeed 配置', KLING_CONFIG_UPDATED: '调整可灵配置',
  UPDATE_PROVIDER_CREDENTIALS: '更新服务授权', REFRESH_MODEL_SCHEMA: '刷新模型配置',
}
const categories = [['IMAGE', '图片生产'], ['VIDEO', '视频生产'], ['CUTFLOW', '视频剪辑'], ['DOWNLOAD', '素材下载'], ['REVIEW', '素材审核'], ['SYSTEM', '账号与系统操作']]
const units: Record<string, string> = { CREATE_VIDEO_TASK: '条', CREATE_IMAGE_TASK: '张', CREATE_CUT_TASK: '条原片', DOWNLOAD_ORIGINAL_VIDEO: '条', DOWNLOAD_CUTFLOW_VIDEO: '条', FINAL_VIDEO_APPROVAL: '条', IMAGE_QUALITY_DECISION: '张' }
const outcomes: Record<string, string> = { APPROVED: '通过', REJECTED: '未通过', PASS: '通过', FAIL: '未通过' }
function activityLabel(event: AccountEvent) {
  const count = event.quantity
  const unit = units[event.action]
  const quantity = unit && typeof count === 'number' && Number.isSafeInteger(count) && count > 0 ? ' ' + count + ' ' + unit : ''
  const outcome = event.outcome && outcomes[event.outcome]
  return (accountActions[event.action] ?? '其他操作') + quantity + (outcome ? ' · ' + outcome : '')
}
const hasDetail = (event: AccountEvent) => !!event.detailTitle?.trim()
type AccountEvent = AccountEventReport['items'][number]
const formatTime = (value: string) => new Date(parseTaskTimestamp(value)).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })


export function AccountEventsPanel({ actorId, users, brands }: { actorId: string; users: AdminUser[]; brands: AccountBrand[] }) {
  const [userId, setUserId] = useState('')
  const [brandId, setBrandId] = useState('')
  const [category, setCategory] = useState('')
  const [period, setPeriod] = useState<UsagePeriod>('week')
  const [today, setToday] = useState(shanghaiToday)
  const [customStart, setCustomStart] = useState(today)
  const [customEnd, setCustomEnd] = useState(today)
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState(20)
  const [refresh, setRefresh] = useState(0)
  const [result, setResult] = useState<{ key: string; report?: AccountEventReport; error?: string } | null>(null)
  const [selection, setSelection] = useState<{ key: string; event: AccountEvent } | null>(null)
  const { startDate, endDate } = usageDateRange(period, today, customStart, customEnd)
  const invalid = !startDate || !endDate || startDate > endDate
  const key = JSON.stringify([actorId, userId, brandId, category, startDate, endDate, offset, limit, refresh])
  const busy = !invalid && result?.key !== key
  const report = result?.key === key ? result.report : undefined
  const error = result?.key === key ? result.error : ''
  const selected = selection?.key === key && report && hasDetail(selection.event) ? selection.event : null
  const showDetails = report?.items.some(hasDetail) ?? false
  useEffect(() => {
    if (invalid) return
    let active = true
    getAccountEvents({ userId, brandId, view: 'production', category, startDate, endDate, offset, limit }, actorId)
      .then((report) => { if (active) setResult(report.view === 'production' ? { key, report } : { key, error: '生产操作记录接口尚未启用。' }) })
      .catch((cause) => { if (active) setResult({ key, error: errorMessage(cause, '操作记录加载失败') }) })
    return () => { active = false }
  }, [actorId, userId, brandId, category, startDate, endDate, offset, limit, key, invalid])
  const filter = (change: () => void) => { change(); setOffset(0); setSelection(null) }
  const matchingBrandIds = brandId ? equivalentBrandIds(brands, brandId) : []
  return <section className="settings-section account-usage-section admin-usage admin-events" aria-label="账号操作记录">
    <div className="admin-usage-toolbar">
      <AdminReportPeriod period={period} today={today} start={customStart} end={customEnd} onPeriod={(value) => filter(() => setPeriod(value))} onRange={(start, end) => filter(() => { setCustomStart(start); setCustomEnd(end) })} />
      <label className="admin-usage-brand"><span className="sr-only">品牌</span><select aria-label="品牌" value={brandId} onChange={(e) => filter(() => { setBrandId(e.target.value); setUserId('') })}><option value="">全部品牌</option>{selectableBrands(brands).map((b) => <option value={b.id} key={b.id}>{b.displayName ?? b.name}</option>)}</select></label>
      <label className="admin-events-filter"><span className="sr-only">操作账号</span><select aria-label="操作账号" value={userId} onChange={(e) => filter(() => setUserId(e.target.value))}><option value="">全部账号</option>{users.filter((u) => !brandId || (u.brandId != null && matchingBrandIds.includes(u.brandId))).map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}</select></label>
      <label className="admin-events-filter"><span className="sr-only">操作类型</span><select aria-label="操作类型" value={category} onChange={(e) => filter(() => setCategory(e.target.value))}><option value="">全部操作</option>{categories.map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
      <button type="button" className="secondary-button admin-usage-refresh" disabled={busy || invalid} onClick={() => { setToday(shanghaiToday()); setRefresh(v => v + 1); setSelection(null) }}><RefreshCw size={16} aria-hidden="true" />刷新记录</button>
    </div>
    {!invalid && <div className="admin-usage-meta"><p className="admin-usage-range">{startDate === endDate ? startDate : startDate + ' 至 ' + endDate}{report ? ' · ' + report.total + ' 条记录' : ''}</p><span className="admin-usage-timezone">北京时间</span></div>}
    {invalid || error ? <div role="alert" className="inline-alert inline-alert--danger">{invalid ? '请选择完整的日期范围，开始日期不能晚于结束日期。' : error}</div> : null}
    {busy ? <div className="admin-usage-loading" role="status"><span className="sr-only">正在读取操作记录…</span><div className="admin-usage-skeleton-table" aria-hidden="true">{[0, 1, 2, 3, 4, 5].map(i => <div key={i}><i /><i /><i /><i /></div>)}</div></div> : null}
    {report && !invalid ? <div className="admin-usage-results">
      <div className="usage-table-scroll" role="region" aria-label="操作记录表" tabIndex={0}><table className={`usage-table usage-account-table admin-usage-table admin-events-table${showDetails ? " has-details" : ""}`}><thead><tr><th scope="col">时间</th><th scope="col">操作账号</th><th scope="col">操作内容</th>{showDetails && <th scope="col">明细</th>}</tr></thead>
        <tbody>{report.items.map((event) => <tr key={event.id}><td><time dateTime={event.createdAt}>{formatTime(event.createdAt)}</time></td><th scope="row">{event.username || '—'}</th><td className="admin-events-activity">{activityLabel(event)}</td>{showDetails && <td>{hasDetail(event) && <button type="button" className="icon-button" aria-label="查看操作详情" title="查看详情" onClick={() => setSelection({ key, event })}><ChevronRight size={18} /></button>}</td>}</tr>)}</tbody></table>
        {!report.items.length ? <p className="account-empty">所选条件下没有操作记录。</p> : null}
      </div>
      <div className="admin-usage-footer"><ReportPagination label="操作记录" offset={offset} limit={limit} total={report.total} onPage={(value) => { setSelection(null); setOffset(value) }} onLimit={(value) => filter(() => setLimit(value))} /></div>
    </div> : null}
    <FoundationDrawer open={selected !== null} title="操作详情" onClose={() => setSelection(null)}>
      {selected && <div className="admin-usage-detail admin-events-detail">
        <div className="admin-events-detail-head"><strong>{activityLabel(selected)}</strong><time dateTime={selected.createdAt}>{formatTime(selected.createdAt)}</time></div>
        <dl><dt>操作账号</dt><dd>{selected.username || '—'}</dd><dt>任务名称</dt><dd>{selected.detailTitle}</dd><dt>任务编号</dt><dd className="admin-events-object-id">{selected.entityId || '—'}</dd></dl>
      </div>}
    </FoundationDrawer>
  </section>
}
