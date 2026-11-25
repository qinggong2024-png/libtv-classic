import { ChevronLeft, ChevronRight, RefreshCw, Image, Film, Scissors } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getAccountUsage } from '../../api'
import { errorMessage } from '../../errorPresentation'
import { formatTime } from '../../domain/taskPresentation'
import type { AccountUsageReport } from '../../types'
import { shanghaiToday, usageDateRange, type UsagePeriod } from './usagePeriod'
import { usagePresentation } from './usagePresentation'
import './accountUsage.css'

const dollars = (amount: number) => new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 6,
}).format(amount)
const periods: { id: UsagePeriod; label: string }[] = [
  { id: 'today', label: '今天' }, { id: 'week', label: '本周' },
  { id: 'month', label: '本月' }, { id: 'custom', label: '自定义' },
]
const pageSize = 10
type Result = { key: string; report?: AccountUsageReport; error?: string }

export function AccountUsagePanel({ actorId }: { actorId?: string }) {
  const [today, setToday] = useState(shanghaiToday)
  const [period, setPeriod] = useState<UsagePeriod>('today')
  const [customStart, setCustomStart] = useState(today)
  const [customEnd, setCustomEnd] = useState(today)
  const [offset, setOffset] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [result, setResult] = useState<Result>()
  const { startDate, endDate } = usageDateRange(period, today, customStart, customEnd)
  const invalidRange = !startDate || !endDate || startDate > endDate
  const key = JSON.stringify([actorId, startDate, endDate, offset, refresh])
  useEffect(() => {
    if (!actorId || invalidRange) return
    let active = true
    getAccountUsage({ userId: actorId, startDate, endDate, status: 'OCCUPIED', offset,
      limit: pageSize, summaryLimit: 1, includeEvents: false }, actorId).then((report) => {
      if (!active) return
      // Never show an all-account report in the personal workspace.
      if (!report.summaries?.some(summary => summary.userId === actorId)) {
        setResult({ key, error: '无法读取个人额度，请刷新重试。' })
        return
      }
      setResult({ key, report })
      if (report.budgetDay) setToday(report.budgetDay)
    }).catch((cause) => {
      if (active) setResult({ key, error: errorMessage(cause, '用量加载失败，请刷新重试。') })
    })
    return () => { active = false }
  }, [actorId, startDate, endDate, offset, refresh, key, invalidRange])
  const current = !invalidRange && result?.key === key ? result : undefined
  const report = current?.report
  const summary = report?.summaries.find(item => item.userId === actorId)
  const busy = Boolean(actorId && !invalidRange && !current)
  const categories = report ? [
    { label: '图片生成', icon: Image, amount: report.imageUsedUsd },
    { label: '视频生成', icon: Film, amount: report.videoUsedUsd },
    { label: '视频剪辑', icon: Scissors, amount: report.cutflowUsedUsd },
    ...(typeof report.otherUsedUsd === 'number' && report.otherUsedUsd !== 0
      ? [{ label: '其他用量', icon: null, amount: report.otherUsedUsd }] : []),
  ] : []
  const changePeriod = (next: UsagePeriod) => { setPeriod(next); setOffset(0) }

  return <section className="settings-section personal-usage" aria-label="个人用量">
    <header className="personal-usage-heading">
      <div><h2>用量概览</h2><small>USD · 预估费用</small></div>
      <button className="icon-button" type="button" title="刷新用量" aria-label="刷新用量" disabled={busy || !actorId || invalidRange} onClick={() => setRefresh(v => v + 1)}><RefreshCw size={17} className={busy ? 'spin' : undefined} /></button>
    </header>
    <div className="personal-usage-period" role="group" aria-label="统计范围"><span className="harmony-segment-thumb" aria-hidden="true"/>
      {periods.map(({ id, label }) => <button type="button" key={id} aria-pressed={period === id} onClick={() => changePeriod(id)}>{label}</button>)}
    </div>
    {period === 'custom' ? <div className="personal-usage-dates">
      <label>开始日期<input type="date" value={customStart} max="9999-12-30" onChange={e => { setCustomStart(e.target.value); setOffset(0) }} /></label>
      <label>结束日期<input type="date" value={customEnd} max="9999-12-30" onChange={e => { setCustomEnd(e.target.value); setOffset(0) }} /></label>
    </div> : null}
    {invalidRange ? <p role="alert">请选择有效的日期范围。</p> : null}
    {!actorId ? <p role="status">请登录后查看个人用量。</p> : null}
    {current?.error ? <div className="inline-alert inline-alert--danger" role="alert">{current.error}</div> : null}
    <div className="personal-usage-metrics" aria-busy={busy}>
      <div><span>{period === 'today' ? '今日已用' : '区间已用'}{report?.unpricedCount ? '（已知）' : ''}</span><strong>{report ? dollars(report.budgetUsedUsd) : '—'}</strong></div>
      <div><span>剩余额度</span><strong>{!summary ? '—' : summary.dailyBudgetUsd === null ? '不限额' : summary.todayRemainingUsd === null || summary.todayUnpricedCount ? '待核对' : dollars(summary.todayRemainingUsd)}</strong>
        {summary && summary.dailyBudgetUsd !== null ? <small>每日额度 {dollars(summary.dailyBudgetUsd)}</small> : null}</div>
    </div>
    {report && (report.unpricedCount > 0 || Boolean(summary?.todayUnpricedCount)) ? <p className="personal-usage-warning" role="status">有费用待核对，金额可能更新。</p> : null}
    <div className="personal-usage-breakdown">
      <h3>费用分布</h3>
      <div className="personal-usage-categories">
        {categories.map(({ label, icon: Icon, amount }) => <div key={label}><span>{Icon ? <Icon size={16} /> : null}{label}</span><strong>{typeof amount === 'number' ? dollars(amount) : '—'}</strong></div>)}
      </div>
      {report && categories.some(item => typeof item.amount !== 'number') ? <small>分类金额暂未更新，可查看下方明细。</small> : null}
      {busy ? <p role="status">正在加载…</p> : null}
    </div>
    <div className="personal-usage-history">
      <h3>用量明细</h3>
      {report?.items.length ? <div className="usage-table-scroll" role="region" aria-label="用量明细"><table className="usage-table personal-usage-table"><thead><tr><th scope="col">时间</th><th scope="col">用途</th><th scope="col">数量</th><th scope="col">金额</th></tr></thead>
        <tbody>{report.items.map(item => <tr key={item.id}><td>{formatTime(item.createdAt)}</td><td>{usagePresentation(item).label}</td><td>{usagePresentation(item).quantity}</td><td>{item.amountUsd === null ? '待核对' : dollars(item.amountUsd)}</td></tr>)}</tbody></table></div> : report ? <p className="personal-usage-empty">暂无用量记录</p> : null}
      {report && report.total > pageSize ? <nav className="personal-usage-pages" aria-label="用量明细分页"><span>共 {report.total} 条</span><button className="icon-button" type="button" aria-label="上一页" disabled={offset === 0} onClick={() => setOffset(v => Math.max(0, v - pageSize))}><ChevronLeft size={18} /></button><button className="icon-button" type="button" aria-label="下一页" disabled={offset + pageSize >= report.total} onClick={() => setOffset(v => v + pageSize)}><ChevronRight size={18} /></button></nav> : null}
    </div>
  </section>
}
