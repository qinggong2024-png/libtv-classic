import { Check, Scissors } from 'lucide-react'

export function CutflowSourceToggle({
  selected,
  onToggle,
  disabled = false,
  disabledLabel,
  actionLabel,
  hint,
  detail = false,
}: {
  selected: boolean
  onToggle: () => void
  disabled?: boolean
  disabledLabel?: string
  actionLabel?: string
  hint?: string
  detail?: boolean
}) {
  const label = disabled ? disabledLabel ?? '不可送剪' : selected ? '已选择送剪' : actionLabel ?? '选择送剪'
  return <label className={`cutflow-source-toggle${selected ? ' is-selected' : ''}${detail ? ' cutflow-source-toggle--detail' : ''}${disabled ? ' is-disabled' : ''}`} title={hint}>
    <input
      type="checkbox"
      checked={selected}
      disabled={disabled}
      onChange={onToggle}
      aria-label={label}
    />
    <span className="cutflow-source-toggle__mark" aria-hidden="true">{selected ? <Check size={13} /> : <Scissors size={13} />}</span>
    <span className="cutflow-source-toggle__copy">
      <strong>{label}</strong>
      {disabled || detail ? <small>{hint ?? (disabled ? '当前剪辑尚未结束，不能重复提交' : selected ? '已加入下方剪辑篮，可继续选择其他原片' : '选择后可单独送剪，也可与同任务其他原片合并')}</small> : null}
    </span>
  </label>
}
