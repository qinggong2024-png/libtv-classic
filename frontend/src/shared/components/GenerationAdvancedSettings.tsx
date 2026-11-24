import { ChevronRight, RotateCcw, SlidersHorizontal } from 'lucide-react'
import type { ReactNode } from 'react'
import './generationSettings.css'

export function GenerationAdvancedSettings({ children, summary, customized, disabled, onReset, alwaysOpen = false }: {
  children: ReactNode
  summary: string
  customized: boolean
  disabled?: boolean
  onReset: () => void
  alwaysOpen?: boolean
}) {
  const scrollToOpenedSettings = (details: HTMLDetailsElement) => {
    if (!details.open) return
    window.requestAnimationFrame(() => {
      if (!details.isConnected || !details.open) return
      const behavior: ScrollBehavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
      details.querySelector('.generation-advanced__body')?.scrollIntoView({ behavior, block: 'start' })
    })
  }

  if (alwaysOpen) return <div className="v6-config-controls">
    <button type="button" className="text-button v6-reset" disabled={disabled} onClick={onReset}><RotateCcw size={14}/>恢复推荐</button>
    <fieldset className="generation-advanced__fields" disabled={disabled}>{children}</fieldset>
  </div>
  return <details className="generation-advanced" onToggle={(event) => {
    if (event.target === event.currentTarget) scrollToOpenedSettings(event.currentTarget)
  }}>
    <summary>
      <SlidersHorizontal size={17} />
      <span className="generation-advanced__title"><strong>高级设置</strong><small>{summary}</small></span>
      <span className={`generation-advanced__state${customized ? ' is-customized' : ''}`}>{customized ? '已自定义' : '推荐设置'}</span>
      <ChevronRight className="generation-advanced__chevron" size={16} />
    </summary>
    <div className="generation-advanced__body">
      <div className="generation-advanced__intro"><p>已为你选好默认方案，需要时再调整。</p><button type="button" className="text-button" disabled={disabled} onClick={onReset}><RotateCcw size={14} />恢复推荐</button></div>
      <fieldset className="generation-advanced__fields" disabled={disabled}>{children}</fieldset>
    </div>
  </details>
}

export function GenerationBrief({ value, onChange, disabled, label = '补充要求（可选）', placeholder }: {
  value: string; onChange: (value: string) => void; disabled?: boolean; label?: string; placeholder: string
}) {
  return <label className="generation-brief"><span>{label}<small>用自己的话描述即可，留空也能生成</small></span><textarea rows={2} maxLength={600} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /></label>
}
