import { useEffect, useState } from 'react'
import { getCutflowSiteChoices, type CutflowSiteChoices } from '../../api'
import { accountSiteSelection } from '../../accountSiteSelection'
import './cutflow.css'

export function CutflowSiteSelect({ value, onChange, disabled = false }: { value: string; onChange: (value: string) => void; disabled?: boolean }) {
  const [choices, setChoices] = useState<CutflowSiteChoices | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void getCutflowSiteChoices(controller.signal).then(result => {
      if (controller.signal.aborted) return
      setChoices(result)
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '站点读取失败')
    })
    return () => controller.abort()
  }, [])
  useEffect(() => {
    if (!choices) return
    const selected = accountSiteSelection(choices, value)
    if (selected !== value) onChange(selected)
  }, [value, choices, onChange])
  return <AccountSiteField value={value} onChange={onChange} disabled={disabled} choices={choices} error={error} />
}

export function AccountSiteField({ value, onChange, disabled = false, choices, error = '', compact = false }: {
  value: string; onChange: (value: string) => void; disabled?: boolean
  choices: CutflowSiteChoices | null; error?: string; compact?: boolean
}) {
  if (!error && choices?.defaultProfileId && choices.sites.some(site => site.id === choices.defaultProfileId)) return null

  return <label className="cutflow-site-field"><span>商品所属品牌</span>
    <select aria-label="商品所属品牌" required value={value} disabled={disabled || !choices} onChange={event => onChange(event.target.value)}>
      <option value="">{choices ? '请选择商品所属品牌' : '正在读取品牌'}</option>
      {choices?.sites.map(site => <option key={site.id} value={site.id}>{site.name}</option>)}
    </select>
    {!compact || error || (choices && !choices.sites.length) ? <small>{error || (choices && !choices.sites.length ? '暂无可用品牌，请联系管理员确认账号归属与品牌站点。' : choices?.defaultProfileId ? '默认使用账号所属品牌，送入剪辑时自动沿用。' : '账号未分配品牌，请选择商品所属品牌。')}</small> : null}
  </label>
}
