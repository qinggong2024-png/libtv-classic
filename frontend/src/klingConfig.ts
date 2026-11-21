import { plainText } from './errorPresentation'
import type { KlingConfig, KlingConfigPatch } from './types'

export type KlingConfigDraft = Omit<KlingConfig, 'apiKeyConfigured' | 'provider' | 'callbackUrl'> & {
  apiKey: string
  clearApiKey: boolean
  callbackUrl: string
}

export const KLING_MODEL_OPTIONS = ['kling-4.0', 'kling-3.0-omni'] as const
export const KLING_BASE_URL_OPTIONS = [
  'https://api-beijing.klingai.com',
  'https://api-singapore.klingai.com',
] as const
export const KLING_ASPECT_RATIO_OPTIONS = ['9:16', '16:9', '1:1', '21:9', 'adaptive'] as const
export const KLING_RESOLUTION_OPTIONS = ['720p', '1080p'] as const
export const KLING_AUDIO_OPTIONS = [
  { value: 'off', label: '关闭' },
  { value: 'native', label: '原生音频' },
  { value: 'original', label: '保留参考视频原声' },
] as const

export type KlingApiStatusPresentation = {
  label: string
  message: string
  tone: 'success' | 'warning' | 'danger' | 'neutral'
}

const KLING_API_STATUS_PRESENTATIONS: Record<string, KlingApiStatusPresentation> = {
  KLING_CLI_READY: {
    label: '已就绪',
    message: 'CLI / MCP 已登录，可使用已开放的可灵模型。',
    tone: 'success',
  },
  KLING_CLI_NOT_AUTHENTICATED: {
    label: '需重新登录',
    message: '请在本机执行 kling login 完成可灵浏览器授权。',
    tone: 'warning',
  },
  KLING_API_READY: {
    label: '已就绪',
    message: '连接检查已通过，可以使用可灵生成服务。',
    tone: 'success',
  },
  KLING_API_CONFIGURED_UNVERIFIED: {
    label: '待验证',
    message: '服务密钥已保存，请点“检查连接”确认能否使用。',
    tone: 'warning',
  },
  KLING_API_AUTHENTICATION_FAILED: {
    label: '密钥验证没通过',
    message: '服务密钥不正确或已失效，请更换后检查连接。',
    tone: 'danger',
  },
  KLING_API_VERIFICATION_FAILED: {
    label: '验证失败',
    message: '暂时连不上可灵，请检查网络和所选地区后再试。',
    tone: 'danger',
  },
  KLING_API_KEY_MISSING: {
    label: '缺少密钥',
    message: '还没有填写可灵服务密钥，请先填写并保存。',
    tone: 'warning',
  },
  KLING_API_DISABLED: {
    label: '未启用',
    message: '可灵服务已关闭，新任务不会发送到可灵。',
    tone: 'neutral',
  },
}

export function klingApiStatusPresentation(
  status: string | undefined,
  enabled = false,
  reason?: string | null,
): KlingApiStatusPresentation {
  if (status && KLING_API_STATUS_PRESENTATIONS[status]) {
    return KLING_API_STATUS_PRESENTATIONS[status]
  }
  return enabled
    ? {
        label: '能力不可用',
        message: plainText(reason, '暂时无法使用可灵，请重新检查连接。'),
        tone: 'danger',
      }
    : KLING_API_STATUS_PRESENTATIONS.KLING_API_DISABLED
}

export function formatKlingApiVersion(value?: string | null): string {
  if (!value) return ''
  const normalized = value.trim()
  return normalized.toLowerCase().startsWith('api-')
    ? `API ${normalized.slice(4)}`
    : normalized
}

export function createKlingConfigDraft(config: KlingConfig): KlingConfigDraft {
  return {
    backend: config.backend,
    baseUrl: config.baseUrl ?? '',
    callbackUrl: config.callbackUrl ?? '',
    model: config.model,
    duration: config.duration,
    aspectRatio: config.aspectRatio,
    resolution: config.resolution,
    audio: config.audio,
    multiShot: config.multiShot,
    maxSubmitConcurrency: config.maxSubmitConcurrency,
    maxPollConcurrency: config.maxPollConcurrency,
    enabled: config.enabled,
    apiKey: '',
    clearApiKey: false,
  }
}

export function validateKlingConfig(
  draft: KlingConfigDraft,
  apiKeyConfigured: boolean,
): string {
  const willHaveApiKey = Boolean(draft.apiKey.trim()) || (apiKeyConfigured && !draft.clearApiKey)
  if (draft.backend === 'api' && draft.enabled && !willHaveApiKey) {
    return '开启可灵前，请先填写服务密钥。'
  }
  const normalizedBaseUrl = draft.baseUrl.trim().replace(/\/+$/, '')
  if (!KLING_BASE_URL_OPTIONS.some((value) => value === normalizedBaseUrl)) {
    return '请选择可灵北京或新加坡服务地址。'
  }
  if (!validCallbackUrl(draft.callbackUrl)) {
    return '结果通知地址请以 https:// 开头，不要包含账号、密码或 # 后面的内容。'
  }
  const isV3 = draft.backend === 'api' && draft.model === 'kling-3.0-omni'
  const maxDuration = isV3 ? 15 : 30
  if (!Number.isInteger(draft.duration) || draft.duration < 3 || draft.duration > maxDuration) {
    return `视频时长必须是 3–${maxDuration} 秒之间的整数。`
  }
  if (isV3 && !['9:16', '16:9', '1:1'].includes(draft.aspectRatio)) {
    return 'Kling 3.0 Omni 仅支持 9:16、16:9 和 1:1。'
  }
  if (!Number.isInteger(draft.maxSubmitConcurrency) || draft.maxSubmitConcurrency < 1 || draft.maxSubmitConcurrency > 20) {
    return '同时提交的任务数必须是 1–20 之间的整数。'
  }
  if (!Number.isInteger(draft.maxPollConcurrency) || draft.maxPollConcurrency < 1 || draft.maxPollConcurrency > 50) {
    return '同时查询的任务数必须是 1–50 之间的整数。'
  }
  return ''
}

function validCallbackUrl(value: string): boolean {
  const normalized = value.trim()
  if (!normalized) return true
  try {
    const url = new URL(normalized)
    return url.protocol === 'https:'
      && !url.username
      && !url.password
      && !url.hash
  } catch {
    return false
  }
}

export function buildKlingConfigPatch(draft: KlingConfigDraft): KlingConfigPatch {
  const apiKey = draft.apiKey.trim()
  return {
    ...(apiKey ? { apiKey } : {}),
    ...(draft.clearApiKey ? { clearApiKey: true } : {}),
    baseUrl: draft.baseUrl.trim().replace(/\/+$/, ''),
    ...(draft.callbackUrl.trim()
      ? { callbackUrl: draft.callbackUrl.trim() }
      : { clearCallbackUrl: true }),
    model: draft.model,
    duration: draft.duration,
    aspectRatio: draft.aspectRatio,
    resolution: draft.resolution,
    audio: draft.audio,
    multiShot: draft.multiShot,
    maxSubmitConcurrency: draft.maxSubmitConcurrency,
    maxPollConcurrency: draft.maxPollConcurrency,
    enabled: draft.enabled,
  }
}
