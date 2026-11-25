// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { type StatusTone, type Task, type Variant, type VariantKind } from '../types'
import { parseTaskTimestamp } from '../taskTiming'
import { providerActionRequiredMessage } from '../providerPresentation'
import { userFacingError } from '../errorPresentation'
import { ANALYSIS_FAILURE_CODES } from './workflowStates'
import { taskStatusPresentation as presentTaskStatus } from './statusPresentation'

export function variantKindShortLabel(kind: VariantKind) {
  return kind === 'STABLE' ? '稳' : '动'
}

export function variantKindDisplayName(kind: VariantKind) {
  return kind === 'STABLE' ? '稳定展示' : '动态广告'
}

export function variantShortLabel(variant: Variant) {
  return variant.variantIndex ? String(variant.variantIndex) : variantKindShortLabel(variant.kind)
}

export function variantDisplayName(variant: Variant) {
  return variant.variantIndex ? `结果 ${variant.variantIndex}` : variantKindDisplayName(variant.kind)
}

export function variantLabelClass(variant: Variant) {
  return variant.variantIndex ? 'variant-label--result' : variant.kind === 'STABLE' ? 'variant-label--a' : 'variant-label--b'
}

export function variantFileToken(variant: Variant) {
  return variant.variantKey ?? variant.kind.toLowerCase()
}

export function variantErrorMessage(variant: Variant) {
  return variant.errorMessage ?? variant.providerErrorMessage ?? null
}

export function variantErrorRetryable(variant: Variant) {
  return variant.retryable ?? variant.providerErrorRetryable ?? null
}

export function variantErrorDetails(variant: Variant) {
  return variant.errorDetails ?? variant.providerErrorDetails ?? null
}

export function formatTime(value: string) {
  return new Date(parseTaskTimestamp(value)).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function isAnalysisFailure(task: Task): boolean {
  return task.phase === 'ACTION_REQUIRED' && ANALYSIS_FAILURE_CODES.has(task.phaseReasonCode ?? '')
}

export function taskStatus(task: Task): { label: string; tone: StatusTone } {
  const meta = presentTaskStatus(task)
  if (!meta.label) {
    console.warn(`[taskStatus] 缺少中文标签映射：phase=${task.phase}，请在 statusMeta 中补充`)
  }
  return { label: meta.label, tone: meta.tone }
}

export function isWaveSpeedBalanceFailure(task: Task): boolean {
  return task.generationProvider?.startsWith('wavespeed') === true
    && task.variants.some(variant =>
      variant.errorCode === 'WAVESPEED_BALANCE_INSUFFICIENT'
      || /insufficient (?:credits|balance)|not enough credits|余额不足/i.test(
        variantErrorMessage(variant) ?? '',
      ),
    )
}

export function isKlingRiskControlFailure(task: Task): boolean {
  return task.generationProvider?.startsWith('kling') === true
    && task.variants.some(variant =>
      variant.errorCode === 'KLING_REMOTE_RISK_CONTROL_REJECTED'
      || /failure to pass the risk control system/i.test(variantErrorMessage(variant) ?? ''),
    )
}

export function isKlingApiBalanceFailure(task: Task): boolean {
  return task.generationProvider?.startsWith('kling') === true
    && task.variants.some(variant =>
      variant.errorCode === 'KLING_API_BALANCE_INSUFFICIENT'
      || (variant.errorCode === 'KLING_API_ACCOUNT_UNAVAILABLE'
        && /(?:balance|credits?|余额|灵感值).*(?:not enough|insufficient|不足|用完)/i.test(variantErrorMessage(variant) ?? '')),
    )
}

export function taskRecoveryHeadline(task: Task): string {
  if (isKlingApiBalanceFailure(task)) return 'API 余额不足'
  if (task.phaseReasonCode === 'KLING_PRIOR_RISK_CONTROL_REJECTED') return '已阻止重复风控提交'
  if (isKlingRiskControlFailure(task)) return '可灵云端风控审核未通过'
  if (isWaveSpeedBalanceFailure(task)) {
    return task.workflowKind === 'DEEP_VIDEO' && task.settings?.deepVideoStage === 'replacement'
      ? '深度提取成功 · 替换阶段余额不足'
      : 'WaveSpeed 账户余额不足'
  }
  return '生成暂时无法继续'
}

export function taskFailureMessage(task: Task): string {
  if (task.phase === 'ACTION_REQUIRED') {
    if (isKlingApiBalanceFailure(task)) {
      return 'API 余额不足，请联系管理员充值。已确认没有提交成功的失败片段会自动恢复；已完成的视频继续保留。状态不明的请求仍须先核对。'
    }
    if (isKlingRiskControlFailure(task)) {
      return '可灵已受理任务，但云端风控审核未通过，未生成视频。服务未说明是哪张图片或哪段提示词触发；请管理员核对参考图和提示词，暂勿直接重试。扣费请以可灵回执与任务账务记录为准。'
    }
    if (isWaveSpeedBalanceFailure(task)) {
      const details = task.variants.find(variant =>
        variant.errorCode === 'WAVESPEED_BALANCE_INSUFFICIENT'
        || /insufficient (?:credits|balance)|not enough credits|余额不足/i.test(
          variantErrorMessage(variant) ?? '',
        ),
      )
      const costs = (details?.providerErrorDetails ?? details?.errorDetails) as Record<string, unknown> | null
      const balance = costs && typeof costs.balanceUsd === 'number' ? costs.balanceUsd : null
      const price = costs && typeof costs.estimatedPriceUsd === 'number' ? costs.estimatedPriceUsd : null
      const quote = balance !== null && price !== null
        ? ` 当前账户余额 $${balance.toFixed(4)}，本阶段报价 $${price.toFixed(4)}，还差 $${Math.max(0, price - balance).toFixed(4)}。`
        : ' 账户余额虽然不为零，但低于本次任务实际需要的费用。'
      return task.workflowKind === 'DEEP_VIDEO' && task.settings?.deepVideoStage === 'replacement'
        ? `深度视频已经提取并保存；人物与商品替换因 WaveSpeed 余额不足而未成功提交。${quote} 请补足余额后点击下方“用于替换”，直接复用深度视频，无需再次支付深度提取费用。`
        : `WaveSpeed 因余额不足拒绝了本阶段生成请求。${quote} 请补足余额后重新尝试。`
    }
    const providerName = task.generationProvider?.startsWith('kling') ? '可灵' : '视频服务'
    if (task.variants.some(variant => variant.generationStatus === 'SUCCEEDED' && variant.downloadStatus === 'READY' && variant.validationStatus === 'FAILED')) {
      return `${providerName}已完成生成，成品也已下载，但本地视频校验未通过。请管理员检查音轨、时长或画幅；无需再次提交付费生成。`
    }
    if (task.variants.some(variant => variant.generationStatus === 'SUCCEEDED' && variant.downloadStatus === 'FAILED')) {
      return `${providerName}已完成生成，但本地下载未成功。请先恢复或重新下载成品，不需要重新生成。`
    }
    if (task.variants.some(variant => variant.generationStatus === 'UNKNOWN')) {
      return `${providerName}的任务状态暂未核实，可能已经提交或扣费。请先核对云端任务，确认前不要重新生成。`
    }
    // The aggregate phase reason hides the actionable error on failed attempts.
    const knownMismatch = task.variants.find((variant) =>
      variant.errorCode === 'KLING_API_VALIDATION_FAILED' &&
      /audio mode ['"]?off['"]? is not supported by the current model/i.test(variantErrorMessage(variant) ?? ''),
    )
    if (knownMismatch) {
      return userFacingError({ code: knownMismatch.errorCode, message: variantErrorMessage(knownMismatch) })
    }
  }
  return providerActionRequiredMessage(task.phaseReasonCode, task.generationProvider)
}
