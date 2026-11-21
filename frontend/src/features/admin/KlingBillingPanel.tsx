import { RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { getKlingBilling, getKlingCredits } from '../../api'
import { errorMessage } from '../../errorPresentation'
import type { KlingBillingReport, KlingCreditsStatus } from '../../types'

function chargeLabel(type: string, packageType: string | null): string {
  const kind = type === 'unit' ? 'unit（可灵用量单位）' : 'cash（币种未注明）'
  return packageType ? kind + ' · ' + packageType : kind
}

export function KlingBillingPanel({ adminId }: { adminId: string }) {
  const [report, setReport] = useState<KlingBillingReport | null>(null)
  const [credits, setCredits] = useState<KlingCreditsStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [billing, balance] = await Promise.allSettled([
        getKlingBilling(adminId), getKlingCredits(adminId),
      ])
      setCredits(balance.status === 'fulfilled'
        ? balance.value : { status: 'unavailable', admissionBlocked: false })
      if (billing.status === 'rejected') throw billing.reason
      setReport(billing.value)
      setError('')
    } catch (cause) {
      setError(errorMessage(cause, '无法读取已保存的可灵计费回执'))
    } finally {
      setLoading(false)
    }
  }, [adminId])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    const interval = window.setInterval(() => void load(), 5 * 60 * 1000)
    return () => { window.clearTimeout(timer); window.clearInterval(interval) }
  }, [load])

  return <section className="settings-section kling-billing-section" aria-label="可灵官方 API 与 CLI 计费回执">
    <div className="settings-section-heading">
      <div>
        <span>只读 · 官方回执</span>
        <h2>可灵 API / CLI 消耗</h2>
        <p>CLI Credits 按 $0.01085／积分计入 ALLWE 内部美元预算。余额仅用于管理员预警，不是生成门槛；历史任务仍按原记录展示。</p>
      </div>
      <button className="secondary-button" type="button" disabled={loading} onClick={() => void load()}>
        <RefreshCw size={16} className={loading ? 'spin' : ''} />刷新计费
      </button>
    </div>
    {credits?.status === 'ready' ? <div className={credits.lowBalance ? 'inline-alert inline-alert--warning' : 'global-usage-pricing-note'} role={credits.lowBalance ? 'alert' : 'status'}>
      可灵 CLI 账户余额：<strong>{credits.availableCredits?.toLocaleString('zh-CN')} 积分</strong>。
      {credits.lowBalance
        ? ' 余额低于建议补充阈值（' + credits.warningThresholdCredits + ' 积分），请管理员及时充值。'
        : ' 当前未触发低余额预警。'}
      {credits.estimatedDaysRemaining != null ? ' 根据 ALLWE 近 7 天积分用量，估计可用 ' + credits.estimatedDaysRemaining + ' 天。' : ''}
      <small>不包括站外生成；仅作提醒，不影响员工提交。</small>
    </div> : null}
    {credits?.status === 'unavailable' ? <p className="global-usage-pricing-note">可灵账户余额暂时无法查询，员工生成不会因此被拦截。</p> : null}
    {error ? <div className="inline-alert inline-alert--danger" role="alert">{error}</div> : null}
    {!report ? <p>{loading ? '正在读取已保存的回执…' : '暂无计费数据'}</p> : <>
      <p className="global-usage-pricing-note">最近 {report.scanLimit} 次可灵 API / CLI 任务内，已扫描 {report.scannedAttempts} 条，发现 {report.receiptCount} 条有凭据的消耗记录。仅覆盖 ALLWE 已入库任务，不含 CLI 外部单独提交的生成；不代表可灵账户总账单。{report.invalidReceiptCount ? '另有 ' + report.invalidReceiptCount + ' 条回执格式异常未计入。' : ''}</p>
      {report.cliCreditReceiptCount > 0 ? <dl className="usage-totals"><div><dt>CLI / MCP 已记录 Credits（{report.cliCreditReceiptCount} 条）</dt><dd>{report.cliCreditsTotal}</dd></div></dl> : null}
      {report.totals.length ? <dl className="usage-totals">
        {report.totals.map((total) => <div key={total.type + ':' + (total.packageType ?? '')}>
          <dt>{chargeLabel(total.type, total.packageType)}</dt>
          <dd>{total.amount}</dd>
        </div>)}
      </dl> : report.cliCreditReceiptCount === 0 ? <p>选定范围内尚无可灵计费回执。</p> : null}
      {report.items.length > 0 ? <div className="usage-table-scroll" role="region" aria-label="可灵计费任务明细" tabIndex={0}>
        <table className="usage-table">
          <thead><tr><th scope="col">任务</th><th scope="col">模型与状态</th><th scope="col">可灵计费</th><th scope="col">记录时间</th></tr></thead>
          <tbody>{report.items.map((item) => <tr key={item.attemptId}>
            <td><strong>{item.taskTitle}</strong><small style={{ display: 'block', overflowWrap: 'anywhere' }}>任务 {item.taskId}</small><small style={{ display: 'block', overflowWrap: 'anywhere' }}>可灵 {item.providerTaskId ?? '未返回 ID'}</small></td>
            <td>{item.model ?? '未知模型'}<small style={{ display: 'block' }}>{item.provider === 'kling_official_cli' ? 'CLI / MCP' : '官方 API'} · {item.status}</small></td>
            <td>{item.creditsConsumed != null ? <div>{item.creditsConsumed} Credits（CLI 回执） · 内部核算 ${(item.creditsConsumed * 0.01085).toFixed(2)}</div> : null}{item.charges.map((charge, index) => <div key={index + '-' + charge.type}>{charge.amount} {chargeLabel(charge.type, charge.packageType)}</div>)}</td>
            <td>{new Date(item.createdAt).toLocaleString('zh-CN')}</td>
          </tr>)}</tbody>
        </table>
      </div> : null}
    </>}
  </section>
}
