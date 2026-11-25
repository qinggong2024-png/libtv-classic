// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { type Task } from '../../types'
import { providerPresentation, providerTaskBadgeLabel, videoModelDisplayName } from '../../providerPresentation'
import { statusPresentation, taskStatusPresentation } from '../../domain/statusPresentation'

export function StatusBadge({ value, retryable, hasArtifact }: { value: string | null; retryable?: boolean | null; hasArtifact?: boolean }) {
  const meta = statusPresentation({ source: 'attempt', value, retryable, hasArtifact })
  return <span className={`status status--${meta.tone}`} title={meta.description} aria-label={`${meta.label}：${meta.description}`}><span />{meta.label}</span>
}

export function ProviderBadge({
  provider,
  workflowKind,
  model,
  mode,
}: {
  provider: string
  workflowKind?: string
  model?: unknown
  mode?: unknown
}) {
  const meta = providerPresentation(provider)
  const isDeepVideo = workflowKind === 'DEEP_VIDEO'
  const isWaveSpeedKlingModel = meta.kind === 'wavespeed' && videoModelDisplayName(model)?.startsWith('Kling') === true
  const label = providerTaskBadgeLabel(provider, workflowKind, model, mode)
  return <span
    className={'provider-badge ' + meta.cssClass}
    title={isDeepVideo || isWaveSpeedKlingModel ? '实际服务：' + meta.displayLabel : undefined}
  >{label}</span>
}

export function TaskStatusBadge({ task }: { task: Task }) {
  const meta = taskStatusPresentation(task)
  return <span className={`status status--${meta.tone}`} title={meta.description} aria-label={`${meta.label}：${meta.description}`}><span />{meta.label}</span>
}
