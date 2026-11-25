import { userFacingError } from './errorPresentation'

export type GenerationProviderKind = 'legacy' | 'kling' | 'wavespeed' | 'bach'

export type ProviderPresentation = {
  kind: GenerationProviderKind
  displayName: string
  displayLabel: string
  cssClass: string
}

export function generationProviderKind(provider?: string | null): GenerationProviderKind {
  const normalized = provider?.trim().toLowerCase() ?? ''
  if (normalized === 'wavespeed' || normalized.startsWith('wavespeed_')) return 'wavespeed'
  if (normalized === 'bach' || normalized.startsWith('bach_')) return 'bach'
  return normalized === 'kling' || normalized.startsWith('kling_') ? 'kling' : 'legacy'
}

export function providerPresentation(provider?: string | null): ProviderPresentation {
  const normalized = provider?.trim().toLowerCase() ?? ''
  if (generationProviderKind(provider) === 'kling') {
    const displayLabel = normalized.includes('_cli') ? '可灵历史任务' : '可灵官方服务'
    return { kind: 'kling', displayName: '可灵', displayLabel, cssClass: 'provider--kling' }
  }
  if (generationProviderKind(provider) === 'wavespeed') {
    return {
      kind: 'wavespeed',
      displayName: 'WaveSpeed',
      displayLabel: 'WaveSpeed 视频服务',
      cssClass: 'provider--wavespeed',
    }
  }
  if (generationProviderKind(provider) === 'bach') {
    return {
      kind: 'bach',
      displayName: 'Bach',
      displayLabel: 'Bach 图片转视频',
      cssClass: 'provider--bach',
    }
  }
  return {
    kind: 'legacy',
    displayName: '历史任务',
    displayLabel: '历史生成通道',
    cssClass: 'provider--legacy',
  }
}

export function videoModelDisplayName(model: unknown): string | null {
  if (typeof model !== 'string') return null
  const normalized = model.trim().toLowerCase()
  if (!normalized) return null
  if (normalized.includes('depth-anything')) return 'Depth Anything'
  if (normalized.includes('seedance-2.5-flash')) return 'Seedance 2.5 Flash'
  if (normalized.includes('seedance-2.0')) return 'Seedance 2.0'
  if (normalized.includes('kling-video-o3')) return 'Kling O3'
  if (normalized.includes('kling')) return 'Kling'
  return null
}

export function deepVideoStageLabel(
  key: string,
  model: unknown,
  depthModel?: unknown,
): string {
  if (key === 'DEPTH_EXTRACTION') {
    return (videoModelDisplayName(depthModel) ?? 'Depth Anything') + ' 深度提取'
  }
  if (key === 'REPLACEMENT' || key === 'GENERATION') {
    return (videoModelDisplayName(model) ?? '深度视频') + ' 人物与商品替换'
  }
  return '深度视频'
}

export function deepVideoTaskDisplayLabel(
  mode: unknown,
  model: unknown,
  depthModel?: unknown,
): string {
  return mode === 'depth'
    ? deepVideoStageLabel('DEPTH_EXTRACTION', model, depthModel)
    : deepVideoStageLabel('REPLACEMENT', model, depthModel)
}

export function deepVideoTaskBadgeLabel(mode: unknown, model: unknown): string {
  return '深度视频 · ' + (videoModelDisplayName(model) ?? (mode === 'depth' ? 'Depth Anything' : '视频替换'))
}

export function providerTaskBadgeLabel(
  provider: string,
  workflowKind: unknown,
  model: unknown,
  mode?: unknown,
): string {
  const meta = providerPresentation(provider)
  if (workflowKind === 'DEEP_VIDEO') return deepVideoTaskBadgeLabel(mode, model)
  const modelName = videoModelDisplayName(model)
  if (meta.kind === 'wavespeed' && modelName?.startsWith('Kling')) return '可灵 · ' + modelName
  return meta.displayLabel
}

export function deepVideoProgressLabel(
  phase: string,
  mode: unknown,
  model: unknown,
  depthModel?: unknown,
  deepVideoStage?: unknown,
  deepVideoPipeline?: unknown,
): string | null {
  const isExtractingDepth = mode === 'depth'
    || (deepVideoPipeline === 'depth-first-v1' && deepVideoStage !== 'replacement')
  const stage = isExtractingDepth
    ? deepVideoStageLabel('DEPTH_EXTRACTION', model, depthModel)
    : deepVideoTaskDisplayLabel(mode, model, depthModel)
  const labels: Record<string, string> = {
    PREPARING: stage + '：准备参考视频与素材',
    QUEUED: stage + '：等待提交',
    GENERATING: stage + '：云端处理中',
    PARTIALLY_DELIVERABLE: stage + '：部分结果已返回',
  }
  return labels[phase] ?? null
}

const PHASE_PROGRESS: Record<string, number> = {
  DRAFT: 8,
  ANALYZING: 28,
  AWAITING_CONFIRMATION: 45,
  PREPARING: 55,
  QUEUED: 64,
  GENERATING: 78,
  PARTIALLY_DELIVERABLE: 92,
  DELIVERABLE: 100,
  ACTION_REQUIRED: 100,
  CANCELLED: 100,
}

export function providerProgress(phase: string, provider?: string | null) {
  const meta = providerPresentation(provider)
  const providerLabel = meta.displayLabel
  const labels: Record<string, string> = {
    PREPARING: meta.kind !== 'legacy'
      ? `${providerLabel}：准备并上传参考图`
      : `${providerLabel}：准备历史任务素材`,
    QUEUED: meta.kind !== 'legacy'
      ? `${providerLabel}：等待提交生成`
      : `${providerLabel}：等待执行`,
    GENERATING: meta.kind !== 'legacy'
      ? `${providerLabel}：云端生成中`
      : `${providerLabel}：生成中`,
    PARTIALLY_DELIVERABLE: `${providerLabel}：部分结果已返回`,
  }
  return {
    ...meta,
    percent: PHASE_PROGRESS[phase] ?? 50,
    label: labels[phase] ?? null,
  }
}

export function providerTimingStageLabel(key: string, provider?: string | null, model?: unknown): string {
  const meta = providerPresentation(provider)
  if (key === 'DEPTH_EXTRACTION') return '深度提取'
  if (key === 'REPLACEMENT') return (videoModelDisplayName(model) ?? '视频模型') + ' 人物与商品替换'
  if (key === 'SOURCE_UPLOAD') return '素材上传'
  if (key === 'ANALYSIS') return '内容分析'
  if (key === 'CONFIRMATION') return '方案确认'
  if (key === 'MATERIALIZATION') {
    return meta.kind !== 'legacy' ? `${meta.displayLabel} 素材准备` : '历史任务素材准备'
  }
  if (key === 'GENERATION') {
    return meta.kind === 'wavespeed'
      ? 'WaveSpeed 云端生成'
      : meta.kind === 'bach'
        ? 'Bach 云端生成'
        : meta.kind === 'kling' ? '可灵云端生成' : '历史任务生成'
  }
  return '成品下载'
}

export function providerActionRequiredMessage(
  reasonCode?: string | null,
  provider?: string | null,
): string {
  const meta = providerPresentation(provider)
  return userFacingError({ code: reasonCode, message: `${meta.displayName} 暂时无法开始生成，请检查素材和设置；仍有问题时请联系管理员。` })
}

type VariantExecution = {
  generationStatus: string
  downloadStatus: string
  validationStatus?: string
  downloadArtifactId: string | null
  errorCode: string | null
  provider?: string | null
  providerTaskId?: string | null
  providerStatus?: string | null
  errorMessage?: string | null
  retryable?: boolean | null
  providerErrorMessage?: string | null
  providerErrorRetryable?: boolean | null
}

export function variantExecutionMessage(
  variant: VariantExecution,
  taskProvider?: string | null,
  taskPhase?: string | null,
): string {
  const meta = providerPresentation(variant.provider ?? taskProvider)
  if (variant.validationStatus === 'FAILED' && variant.generationStatus === 'SUCCEEDED') {
    return `${meta.displayName}已生成视频，但本地成品校验未通过；请先检查已有文件，不要重新提交生成。`
  }
  if (variant.downloadArtifactId) return '成品文件已就绪'
  if (variant.providerStatus === 'waiting_balance' && variant.generationStatus === 'PENDING') {
    return 'API 余额不足，请联系管理员充值。充值后后台会自动恢复未提交成功的片段。'
  }
  if (variant.providerStatus === 'waiting_capacity' && variant.generationStatus === 'PENDING') {
    return '等待开始生成'
  }
  if (variant.downloadStatus === 'DOWNLOADING') return `${meta.displayLabel} 已返回结果，正在下载成品`
  if (meta.kind === 'wavespeed' && (
    variant.errorCode === 'WAVESPEED_BALANCE_INSUFFICIENT'
    || /insufficient (?:credits|balance)|not enough credits|余额不足/i.test(
      variant.errorMessage ?? variant.providerErrorMessage ?? '',
    )
  )) return 'WaveSpeed 账户余额不足，本阶段请求已被拒绝；已保存的深度视频可以继续复用。'
  if (variant.generationStatus === 'UNKNOWN') {
    return meta.kind !== 'legacy'
      ? `${meta.displayName} 的任务状态暂时无法确认，请先核对任务和扣费情况，不要重复生成。`
      : '这条历史任务的状态暂时无法确认，请先核对任务和扣费情况，不要重复生成。'
  }
  if (variant.errorCode || variant.generationStatus === 'FAILED' || variant.errorMessage || variant.providerErrorMessage) {
    const retryable = variant.retryable ?? variant.providerErrorRetryable
    const retry = retryable === true
      ? '可以重试'
      : retryable === false
        ? '请联系管理员检查，先不要重新生成'
        : ''
    return [
      userFacingError({ code: variant.errorCode, message: variant.errorMessage ?? variant.providerErrorMessage }),
      retry,
    ].filter(Boolean).join(' · ')
  }
  if (taskPhase === 'ACTION_REQUIRED' && !variant.providerTaskId) {
    return `${meta.displayName}：任务暂时无法继续，请查看问题说明，或联系管理员。`
  }
  if (variant.providerTaskId) {
    return meta.kind !== 'legacy'
      ? `${meta.displayName} 已收到任务，正在等待结果。`
      : '历史任务已提交，正在等待结果。'
  }
  if (variant.generationStatus === 'PENDING') {
    return meta.kind !== 'legacy' ? `等待 ${meta.displayLabel} 提交生成` : '等待历史任务执行'
  }
  return meta.kind !== 'legacy'
    ? `正在等待 ${meta.displayLabel} 返回生成状态`
    : '正在等待历史任务返回状态'
}
