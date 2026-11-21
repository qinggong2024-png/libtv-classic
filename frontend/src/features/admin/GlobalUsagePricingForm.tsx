import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { getGlobalUsagePricing, saveGlobalUsagePricing } from '../../api'
import type { GlobalUsagePricing } from '../../types'

const priceFields = [
  ['imagePriceUsd', '每张图片预估（USD）'],
  ['videoPricePerSecondUsd', '每秒视频预估（USD）'],
  ['depthVideoPricePerSecondUsd', '深度提取每秒预估（USD）'],
  ['recreateVideoPricePerSecondUsd', '人物／产品替换每秒预估（USD）'],
  ['cutflowPriceUsd', '每条剪辑预估（USD）'],
] as const
type PriceKey = typeof priceFields[number][0]
type PriceDraft = Record<PriceKey, string>

export function GlobalUsagePricingForm({ actorId }: { actorId: string }) {
  const [pricing, setPricing] = useState<GlobalUsagePricing | null>(null)
  const [draft, setDraft] = useState<PriceDraft>({ imagePriceUsd: '', videoPricePerSecondUsd: '', depthVideoPricePerSecondUsd: '', recreateVideoPricePerSecondUsd: '', cutflowPriceUsd: '' })
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const applyPricing = (value: GlobalUsagePricing) => {
    setPricing(value)
    setDraft({ imagePriceUsd: value.imagePriceUsd?.toString() ?? '', videoPricePerSecondUsd: value.videoPricePerSecondUsd?.toString() ?? '', depthVideoPricePerSecondUsd: value.depthVideoPricePerSecondUsd?.toString() ?? '', recreateVideoPricePerSecondUsd: value.recreateVideoPricePerSecondUsd?.toString() ?? '', cutflowPriceUsd: value.cutflowPriceUsd?.toString() ?? '' })
  }
  const load = useCallback(async () => {
    setBusy(true); setError(''); setNotice('')
    try { applyPricing(await getGlobalUsagePricing(actorId)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '无法读取全站单价，请重试') }
    finally { setBusy(false) }
  }, [actorId])
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!pricing) return
    setBusy(true); setError(''); setNotice('')
    const money = (value: string) => value.trim() === '' ? null : Number(value)
    try {
      applyPricing(await saveGlobalUsagePricing(actorId, { version: pricing.version, imagePriceUsd: money(draft.imagePriceUsd), videoPricePerSecondUsd: money(draft.videoPricePerSecondUsd), depthVideoPricePerSecondUsd: money(draft.depthVideoPricePerSecondUsd), recreateVideoPricePerSecondUsd: money(draft.recreateVideoPricePerSecondUsd), cutflowPriceUsd: money(draft.cutflowPriceUsd) }))
      setNotice('全站单价已保存，所有账号的新任务均使用此单价。')
    } catch (cause) { setError(cause instanceof Error ? cause.message : '保存失败，请重试') }
    finally { setBusy(false) }
  }
  return <section className="settings-section global-usage-pricing-section">
    <div className="settings-section-heading"><div><h2>全站生成单价</h2><p>每类单价只需填写一次，所有已有和新建账号统一使用。各账号单独设置每日预算与权限。</p></div><button type="button" className="secondary-button" disabled={busy} onClick={() => void load()}>刷新全站单价</button></div>
    <form onSubmit={(event) => void save(event)} aria-label="全站生成单价" aria-busy={busy}>
      <div className="account-policy-fields">
        {priceFields.map(([key, label]) => <label key={key}>{label}<input type="number" min="0" max="10000" step="0.000001" disabled={busy || !pricing} value={draft[key]} onChange={(event) => { setDraft((current) => ({ ...current, [key]: event.target.value })); setNotice('') }} placeholder="未定价" />{pricing?.legacyConflicts.includes(key) ? <small>旧账号单价不一致，请填写统一单价。</small> : null}</label>)}
      </div>
      <p className="global-usage-pricing-note">图片按张数，视频按目标秒数，剪辑按原片条数计价；留空为未定价，0 为零金额估算。有预算的账号遇到未定价任务时停止提交（含剪辑）。新单价只影响新任务；估算不含分析费，以供应商账单为准。</p>
      <div aria-live="polite">{error ? <div className="inline-alert inline-alert--danger" role="alert">{error}</div> : null}{notice ? <p role="status">{notice}</p> : null}</div>
      <div className="account-actions"><button className="primary-button" type="submit" disabled={busy || !pricing}>{busy ? (pricing ? '正在保存…' : '正在读取…') : '保存全站单价'}</button></div>
    </form>
  </section>
}
