import type { AdminCredentialsStatus, AdminUser, AuthUser, BachConfig, BachConfigPatch, BatchConfirmationResponse, FeedbackReason, FeedbackValue, FixedVideoTaskOptions, ImageGenerationBatch, ImageGenerationClientManifest, KlingConfig, KlingConfigPatch, KlingElement, KlingElementCreate, KlingElementList, KlingStatus, LibTVAccountStatus, LibTVAccountSwitch, LibTVLoginStart, MotionActionSummary, MotionActionWrite, AdminVideoPage, AdminVideoTaskPage, AdminCutDeliverable, AdminVideoTrace, AdminVideoHealthFilter, AdminVideoSort, OperationsIssueCategory, OperationsIssuePage, OperationsSnapshot, PromptAssistantResponse, PromptPreset, PromptPresetWrite, Reconciliation, RuntimeConfigSnapshot, SiteStyleProfileSummary, SiteStyleProfileWrite, SystemStatus, Task, UploadResult, VariantKind, VideoLearningPreference, VideoTemplateSummary, WaveSpeedConfig, WaveSpeedConfigPatch } from './types'
import { createUuid } from './uuid'
import { userFacingError, validationMessage } from './errorPresentation'
import type { AccountBrand, AccountEventReport, AccountPolicy, AccountUsageReport, GenerationBudget, GlobalUsagePricing } from './types'

const DEFAULT_USER_ID = '00000000-0000-0000-0000-000000000001'
const DEFAULT_TIMEOUT_MS = 30_000
const UPLOAD_TIMEOUT_MS = 60_000
const LONG_RUNNING_TIMEOUT_MS = 120_000
// Automatic mode performs the single multimodal planning call before task creation.
// Longer than the 225s backend cap but below the LAN proxy's 300s timeout.
const AI_VIDEO_TASK_TIMEOUT_MS = 270_000
const DOWNLOAD_TIMEOUT_MS = 300_000
// 生产构建不内置兜底身份：未建立登录态之前，请求就不该带着一个固定 UUID 出去。
// 开发构建保留默认值，方便本地直连后端调试（该模式没有 cookie session）。
let developmentUserId: string | null = import.meta.env.PROD ? null : DEFAULT_USER_ID
let csrfCookieName = 'libtv_csrf'
let csrfToken = ''

type RequestPolicy = {
  authenticate?: boolean
  actorId?: string | null
  timeoutMs?: number
  onResponse?: (response: Response) => void
}

export class ApiError extends Error {
  code: string
  status: number
  details: unknown
  retryable: boolean | null
  mayHaveBeenAccepted: boolean
  mayHaveBeenCharged: boolean
  requiresReconciliation: boolean
  correlationId: string | null
  rawMessage: string

  constructor(
    status: number,
    code: string,
    message: string,
    details: unknown = null,
    retryable: boolean | null = null,
    mayHaveBeenAccepted = false,
    mayHaveBeenCharged = false,
    requiresReconciliation = false,
    correlationId: string | null = null,
  ) {
    super(userFacingError({ status, code, message, mayHaveBeenAccepted, mayHaveBeenCharged, requiresReconciliation }))
    this.rawMessage = message
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.retryable = retryable
    this.mayHaveBeenAccepted = mayHaveBeenAccepted
    this.mayHaveBeenCharged = mayHaveBeenCharged
    this.requiresReconciliation = requiresReconciliation
    this.correlationId = correlationId
  }
}

export function setApiUserId(value: string | null) {
  developmentUserId = value
}

export function setCsrfCookieName(value: string | null) {
  if (value) csrfCookieName = value
}

function readCookie(name: string): string {
  if (typeof document === 'undefined') return ''
  const prefix = `${encodeURIComponent(name)}=`
  const match = document.cookie.split('; ').find((item) => item.startsWith(prefix))
  return match ? decodeURIComponent(match.slice(prefix.length)) : ''
}

type ErrorPayload = {
  code?: unknown
  message?: unknown
  details?: unknown
  fieldErrors?: unknown
  retryable?: unknown
  mayHaveBeenAccepted?: unknown
  mayHaveBeenCharged?: unknown
  requiresReconciliation?: unknown
  correlationId?: unknown
}

function validationDetailMessage(detail: unknown[]): string {
  return validationMessage(detail)
}

function apiErrorFromBody(status: number, statusText: string, body: unknown): ApiError {
  const envelope = body && typeof body === 'object' && !Array.isArray(body)
    ? body as Record<string, unknown>
    : {}
  const rawDetail = envelope.detail ?? envelope.error ?? body
  if (typeof rawDetail === 'string') {
    return new ApiError(status, 'REQUEST_FAILED', rawDetail)
  }
  if (Array.isArray(rawDetail)) {
    return new ApiError(
      status,
      'VALIDATION_FAILED',
      validationDetailMessage(rawDetail) || `${status} ${statusText}`,
      rawDetail,
      false,
    )
  }
  const detail = rawDetail && typeof rawDetail === 'object'
    ? rawDetail as ErrorPayload
    : {}
  const code = typeof detail.code === 'string' ? detail.code : 'REQUEST_FAILED'
  const message = code === 'REQUEST_VALIDATION_FAILED' && Array.isArray(detail.fieldErrors)
    ? validationDetailMessage(detail.fieldErrors)
    : typeof detail.message === 'string' ? detail.message : String(status) + ' ' + statusText
  return new ApiError(
    status,
    code,
    message,
    detail.details ?? detail.fieldErrors ?? null,
    typeof detail.retryable === 'boolean' ? detail.retryable : null,
    detail.mayHaveBeenAccepted === true,
    detail.mayHaveBeenCharged === true,
    detail.requiresReconciliation === true,
    typeof detail.correlationId === 'string' ? detail.correlationId : null,
  )
}

async function fetchResponse(path: string, init: RequestInit = {}, policy: RequestPolicy = {}): Promise<Response> {
  const { authenticate = true, actorId = developmentUserId, timeoutMs = DEFAULT_TIMEOUT_MS } = policy
  const headers = new Headers(init.headers)
  if (authenticate && actorId) headers.set('X-User-Id', actorId)
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes((init.method ?? 'GET').toUpperCase())) {
    const token = csrfToken || readCookie(csrfCookieName)
    if (token) headers.set('X-CSRF-Token', token)
  }
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const controller = new AbortController()
  let timedOut = false
  const abortFromCaller = () => controller.abort(init.signal?.reason)
  if (init.signal?.aborted) abortFromCaller()
  else init.signal?.addEventListener('abort', abortFromCaller, { once: true })
  const timeout = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  let response: Response
  try {
    response = await fetch(path, { ...init, headers, credentials: 'include', signal: controller.signal })
  } catch (cause) {
    if (timedOut && cause instanceof DOMException && cause.name === 'AbortError') {
      throw new ApiError(408, 'REQUEST_TIMEOUT', `等待了 ${Math.round(timeoutMs / 1000)} 秒，网站还没有回应。请先查看任务记录，确认是否提交成功后再操作。`)
    }
    throw cause
  } finally {
    window.clearTimeout(timeout)
    init.signal?.removeEventListener('abort', abortFromCaller)
  }
  policy.onResponse?.(response)
  if (init.method && init.method.toUpperCase() !== 'GET' && /^\/api\/v1\/(tasks|video-tasks|deep-video-tasks|image-generation-batches|admin\/usage-pricing|admin\/users)/.test(path)) {
    window.dispatchEvent?.(new Event('generation-budget-refresh'))
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw apiErrorFromBody(response.status, response.statusText, body)
  }
  return response
}

async function request<T>(path: string, init: RequestInit = {}, policy: RequestPolicy = {}): Promise<T> {
  const response = await fetchResponse(path, init, policy)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

async function requestBlob(path: string, policy: RequestPolicy = {}): Promise<Blob> {
  const response = await fetchResponse(path, {}, policy)
  return response.blob()
}

const key = () => createUuid()

export type CreativeV25PreviewResult = {
  taskId: string
  model: 'gpt-6.1-sol' | 'gpt-6.1-terra'
  variantCount: number
  storyboardCount: number
  productCount: number
  durationSeconds: number
  elapsedSeconds: number
  creativeMode: 'showcase' | 'usp'
  klingSubmitted: false
  variants: Array<{
    idea: string
    hook: string
    buyerReason: string
    prompt: string
    klingPromptWithinLimit: boolean
    beats: Array<Record<string, unknown>>
    visualFacts: Array<Record<string, unknown>>
    plannerVersion: string
    requestAttempts: number
  }>
}

export function previewCreativeV25(
  adminId: string,
  options: {
    taskId: string
    model: 'gpt-6.1-sol' | 'gpt-6.1-terra'
    variantCount: 1 | 2
    creativeMode: 'showcase' | 'usp'
    confirmModelCost: true
  },
): Promise<CreativeV25PreviewResult> {
  return request<CreativeV25PreviewResult>(
    '/api/v1/admin/creative-v25-preview',
    { method: 'POST', body: JSON.stringify(options) },
    { actorId: adminId, timeoutMs: 325_000 },
  )
}

export const getGenerationBudget = (signal?: AbortSignal) => request<GenerationBudget>('/api/v1/account/generation-budget', { signal })

export const getSystemStatus = () => request<SystemStatus>('/api/v1/system/status', {}, { authenticate: false })
export const getCurrentUser = () => request<AuthUser>('/api/v1/auth/me')
export const getPersonalPromptAutoConfirmation = () => request<{ automatic: boolean }>('/api/v1/auth/preferences/prompt-confirmation')
export const setPersonalPromptAutoConfirmation = (automatic: boolean) => request<{ automatic: boolean }>('/api/v1/auth/preferences/prompt-confirmation', {
  method: 'PATCH',
  body: JSON.stringify({ automatic }),
})
export const getPersonalVideoLearning = () => request<VideoLearningPreference>('/api/v1/auth/preferences/video-learning')
export const setPersonalVideoLearning = (enabled: boolean) => request<VideoLearningPreference>('/api/v1/auth/preferences/video-learning', {
  method: 'PATCH',
  body: JSON.stringify({ enabled }),
})
export const clearPersonalVideoLearning = () => request<void>('/api/v1/auth/preferences/video-learning', {
  method: 'DELETE',
})
export type TaskPage = { items: Task[]; nextCursor: string | null }

export async function getTaskPage(cursor?: string): Promise<TaskPage> {
  let nextCursor: string | null = null
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
  const items = await request<Task[]>(`/api/v1/tasks${query}`, {}, {
    onResponse: (response) => {
      nextCursor = response.headers.get('X-Next-Cursor')
    },
  })
  return { items, nextCursor }
}

export const getTasks = async () => (await getTaskPage()).items
export const getTask = (taskId: string) => request<Task>(`/api/v1/tasks/${taskId}`)

export const setVariantFeedback = (
  taskId: string,
  variantId: string,
  feedback: FeedbackValue | null,
  details?: { reasons: FeedbackReason[]; comment: string | null },
) => request<Task>(`/api/v1/tasks/${taskId}/variants/${variantId}/feedback`, {
  method: 'PUT',
  body: JSON.stringify({ feedback, ...details }),
})

export const setVariantFinalApproval = (
  taskId: string,
  variantId: string,
  payload: {
    decision: 'APPROVED' | 'REJECTED'
    productFidelityPass: boolean
    editingAssemblyPass: boolean
    humanCriticalPass: boolean
    humanReviewSeconds: number
    expectedStateVersion: number
    notes?: string | null
  },
) => request<Task>(`/api/v1/tasks/${taskId}/variants/${variantId}/final-approval`, {
  method: 'PUT',
  body: JSON.stringify(payload),
})

export async function login(username: string, password: string): Promise<void> {
  const result = await request<{ csrfToken: string }>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }, { authenticate: false })
  csrfToken = result.csrfToken
}

export async function logout(): Promise<void> {
  await request<void>('/api/v1/auth/logout', { method: 'POST' })
  csrfToken = ''
}

export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return request<void>('/api/v1/auth/password', {
    method: 'POST',
    body: JSON.stringify({ currentPassword, newPassword }),
  })
}

export async function uploadStoryboard(file: File, signal?: AbortSignal, sourceImageOutputId?: string): Promise<UploadResult> {
  const body = new FormData()
  body.append('file', file)
  const headers: Record<string, string> = { 'Idempotency-Key': key() }
  if (sourceImageOutputId) headers['X-Source-Image-Output-Id'] = sourceImageOutputId
  return request('/api/v1/uploads', { method: 'POST', body, headers, signal }, { timeoutMs: UPLOAD_TIMEOUT_MS })
}

export function getVideoTemplates(): Promise<VideoTemplateSummary[]> {
  return request('/api/v1/video-templates')
}

export function getSiteStyleProfiles(): Promise<SiteStyleProfileSummary[]> {
  return request('/api/v1/site-style-profiles')
}

export function getMotionActions(): Promise<MotionActionSummary[]> {
  return request('/api/v1/motion-actions')
}

export function createMotionAction(payload: MotionActionWrite): Promise<MotionActionSummary> {
  return request('/api/v1/motion-actions', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateMotionAction(id: string, payload: MotionActionWrite): Promise<MotionActionSummary> {
  return request(`/api/v1/motion-actions/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function createSiteStyleProfile(payload: SiteStyleProfileWrite): Promise<SiteStyleProfileSummary> {
  return request('/api/v1/site-style-profiles', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateSiteStyleProfile(id: string, payload: SiteStyleProfileWrite): Promise<SiteStyleProfileSummary> {
  return request(`/api/v1/site-style-profiles/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function createTask(
  title: string,
  requirement: string,
  uploads: UploadResult[],
  productUploads: UploadResult[],
  preferences: { analysisModel: string; generationProvider: 'libtv' | 'kling'; variants: VariantKind[] },
  sourceUploadDurationSeconds = 0,
) {
  return request<Task>('/api/v1/tasks', {
    method: 'POST',
    headers: { 'Idempotency-Key': key() },
    body: JSON.stringify({
      clientTaskId: createUuid(),
      title,
      userRequirement: requirement,
      analysisModel: preferences.analysisModel,
      generationProvider: preferences.generationProvider,
      sourceUploadDurationSeconds,
      generationPreset: 'DEFAULT_10S_9X16',
      variants: preferences.variants,
      storyboards: uploads.map((upload, index) => ({ uploadId: upload.id, order: index + 1, required: true })),
      productImages: productUploads.map((upload, index) => ({ uploadId: upload.id, order: index + 1 })),
    }),
  })
}

export function createVideoTask(
  title: string,
  uploads: UploadResult[],
  options: FixedVideoTaskOptions,
  sourceUploadDurationSeconds = 0,
  identity: { clientTaskId?: string; idempotencyKey?: string } = {},
) {
  return request<Task>('/api/v1/video-tasks', {
    method: 'POST',
    headers: { 'Idempotency-Key': identity.idempotencyKey ?? key() },
    body: JSON.stringify({
      clientTaskId: identity.clientTaskId ?? createUuid(),
      title,
      storyboards: uploads.map((upload, index) => ({ uploadId: upload.id, order: index + 1, required: true })),
      subjectImages: (options.subjectUploads ?? []).map((upload, index) => ({
        uploadId: upload.id,
        order: index + 1,
      })),
      generationMode: options.generationMode,
      directorMode: options.generationMode === 'automatic' ? options.directorMode ?? 'cross_scene_v2' : undefined,
      videoType: options.generationMode === 'automatic' ? options.videoType ?? 'ootd' : 'ootd',
      solAnalyzeProductImages: options.generationMode === 'automatic' && (options.subjectUploads?.length ?? 0) > 0,
      displayFocus: options.generationMode === 'automatic' ? options.displayFocus ?? 'overall' : undefined,
      creativeBrief: options.creativeBrief,
      customPrompt: options.generationMode === 'custom' ? options.customPrompt : undefined,
      templateId: options.templateId,
      siteProfileId: options.siteProfileId || undefined,
      elementIds: options.elementIds,
      resultCount: options.resultCount,
      durationSeconds: options.durationSeconds,
      actionIntensity: options.actionIntensity,
      cutMode: options.cutMode,
      autoSubmitToCutflow: options.autoSubmitToCutflow ?? true,
      paidSubmissionConsent: true,
      sourceUploadDurationSeconds,
    }),
  }, { timeoutMs: options.generationMode === 'automatic' ? AI_VIDEO_TASK_TIMEOUT_MS : DEFAULT_TIMEOUT_MS })
}

export interface DeepVideoStatus { configured: boolean; enabled: boolean; reason: string | null; recreateModel?: string }
export type DeepVideoMode = 'recreate' | 'depth'
export function getDeepVideoStatus(signal?: AbortSignal): Promise<DeepVideoStatus> {
  return request<DeepVideoStatus>('/api/v1/video-tasks/deep/status', { signal }).catch((cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 404) {
      return { configured: false, enabled: false, reason: '视频复刻通道正在准备中，请稍后使用。' }
    }
    throw cause
  })
}
export function uploadReferenceVideo(file: File, signal?: AbortSignal): Promise<UploadResult & { durationSeconds: number }> {
  const body = new FormData()
  body.append('file', file)
  return request('/api/v1/uploads/video', {
    method: 'POST', body, signal, headers: { 'Idempotency-Key': key() },
  }, { timeoutMs: UPLOAD_TIMEOUT_MS })
}
export function createDeepVideoTask(payload: {
  clientTaskId: string; title: string; referenceVideoUploadId: string;
  referenceVideoType?: 'original' | 'depth';
  mode: DeepVideoMode; subjectImages: { uploadId: string; order: number }[];
  backgroundImageUploadId?: string;
  prompt: string; aspectRatio: '9:16' | '16:9'; paidSubmissionConsent: boolean;
  siteProfileId?: string;
}, idempotencyKey: string): Promise<Task> {
  return request('/api/v1/video-tasks/deep', {
    method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(payload),
  }, { timeoutMs: UPLOAD_TIMEOUT_MS })
}

export type SubmissionRecoveryResult = {
  state: 'submitted' | 'in_progress' | 'unknown' | 'not_found'
  source: string
  checkedAt: string
  auditId: string
  resourceId: string | null
  resourceStatus: string | null
  stateVersion: number | null
  retryAllowed: false
}

export function querySubmissionRecovery(identity: {
  kind: 'video' | 'image'
  clientId?: string
  idempotencyKey?: string
}): Promise<SubmissionRecoveryResult> {
  return request('/api/v1/submission-recovery/query', {
    method: 'POST',
    body: JSON.stringify(identity),
  }, { timeoutMs: DEFAULT_TIMEOUT_MS })
}

export function getPromptPresets(scope: PromptPreset['scope'] = 'video') {
  return request<PromptPreset[]>(`/api/v1/prompt-presets?scope=${encodeURIComponent(scope)}`)
}

export function createPromptPreset(payload: PromptPresetWrite) {
  return request<PromptPreset>('/api/v1/prompt-presets', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updatePromptPreset(id: string, payload: PromptPresetWrite) {
  return request<PromptPreset>(`/api/v1/prompt-presets/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deletePromptPreset(id: string) {
  return request<void>(`/api/v1/prompt-presets/${id}`, { method: 'DELETE' })
}

export function reuseTask(task: Task, variants: VariantKind[]) {
  return request<Task>(`/api/v1/tasks/${task.id}/reuse`, {
    method: 'POST',
    headers: { 'Idempotency-Key': key() },
    body: JSON.stringify({
      clientTaskId: createUuid(),
      title: `${task.title} · 复用`,
      expectedSourceStateVersion: task.stateVersion,
      variants,
    }),
  })
}

export const analyzeTask = (task: Task) => request<Task>(`/api/v1/tasks/${task.id}/analyze`, {
  method: 'POST',
  headers: { 'Idempotency-Key': key() },
  body: JSON.stringify({ expectedStateVersion: task.stateVersion }),
})

export const confirmTask = (task: Task, variants?: VariantKind[]) => request<Task>(`/api/v1/tasks/${task.id}/confirm`, {
  method: 'POST',
  headers: { 'Idempotency-Key': key() },
  body: JSON.stringify({
    expectedStateVersion: task.stateVersion,
    acceptRisk: task.consistencyLevel === 'QUALITY_REVIEW',
    ...(variants ? { variants } : {}),
  }),
}, { timeoutMs: LONG_RUNNING_TIMEOUT_MS })

export const saveTaskPrompts = (task: Task, stablePrompt: string, dynamicPrompt: string) => request<Task>(`/api/v1/tasks/${task.id}/prompts`, {
  method: 'PATCH',
  body: JSON.stringify({
    expectedStateVersion: task.stateVersion,
    stablePrompt,
    dynamicPrompt,
  }),
})

export const runPromptAssistant = (
  task: Task,
  action: 'REGENERATE' | 'ADVISE',
  instruction: string,
  stablePrompt: string,
  dynamicPrompt: string,
  targetVariant?: VariantKind,
) => request<PromptAssistantResponse>(`/api/v1/tasks/${task.id}/prompt-assistant`, {
  method: 'POST',
  body: JSON.stringify({
    expectedStateVersion: task.stateVersion,
    action,
    instruction,
    stablePrompt,
    dynamicPrompt,
    targetVariant,
  }),
}, { timeoutMs: LONG_RUNNING_TIMEOUT_MS })

export const cancelTask = (taskId: string) => request<void>(`/api/v1/tasks/${taskId}/cancel`, {
  method: 'POST',
  headers: { 'Idempotency-Key': key() },
})

export const confirmTasks = (tasks: Task[]) => request<BatchConfirmationResponse>('/api/v1/confirmations/execute', {
  method: 'POST',
  headers: { 'Idempotency-Key': key() },
  body: JSON.stringify({ tasks: tasks.map((task) => ({ taskId: task.id, expectedStateVersion: task.stateVersion, acceptRisk: task.consistencyLevel === 'QUALITY_REVIEW' })) }),
}, { timeoutMs: LONG_RUNNING_TIMEOUT_MS })

export const restoreOutput = (taskId: string, variant: VariantKind) => request<{ artifactId: string; downloadUrl: string }>(`/api/v1/tasks/${taskId}/restore-remote-output`, {
  method: 'POST',
  headers: { 'Idempotency-Key': key() },
  body: JSON.stringify({ variant }),
}, { timeoutMs: LONG_RUNNING_TIMEOUT_MS })

export const downloadUrl = (artifactId: string) => `/api/v1/downloads/${artifactId}`

export async function saveArtifact(artifactId: string, filename: string): Promise<void> {
  saveBlob(await requestBlob(downloadUrl(artifactId), { timeoutMs: DOWNLOAD_TIMEOUT_MS }), filename)
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.hidden = true
  document.body.appendChild(anchor)
  try {
    anchor.click()
  } finally {
    anchor.remove()
    // Chromium may start reading the blob after the click handler returns.
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }
}

export async function fetchAssetObjectUrl(path: string): Promise<string> {
  return URL.createObjectURL(await requestBlob(path, { timeoutMs: UPLOAD_TIMEOUT_MS }))
}

export async function saveAsset(path: string, filename: string): Promise<void> {
  const response = await fetchResponse(path, {}, { timeoutMs: DOWNLOAD_TIMEOUT_MS })
  // CUT download responses own the stable brand/date/sequence filename.
  if (path.startsWith('/api/v1/cutflow-handoffs/')) {
    const disposition = response.headers.get('Content-Disposition') || ''
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
    const plain = /filename="([^"]+)"/i.exec(disposition)?.[1]
    try { filename = encoded ? decodeURIComponent(encoded) : plain || filename } catch { /* Keep fallback. */ }
  }
  saveBlob(await response.blob(), filename)
}

export async function fetchAssetFile(path: string, filename: string): Promise<File> {
  const blob = await requestBlob(path, { timeoutMs: UPLOAD_TIMEOUT_MS })
  return new File([blob], filename, { type: blob.type || 'image/jpeg' })
}

export async function fetchImageAssetFile(path: string, basename: string): Promise<File> {
  const blob = await requestBlob(path, { timeoutMs: UPLOAD_TIMEOUT_MS })
  const extension = blob.type === 'image/png' ? 'png' : blob.type === 'image/webp' ? 'webp' : 'jpg'
  return new File([blob], `${basename}.${extension}`, { type: blob.type || 'image/jpeg' })
}

export async function fetchArtifactObjectUrl(artifactId: string): Promise<string> {
  return URL.createObjectURL(await requestBlob(downloadUrl(artifactId), { timeoutMs: DOWNLOAD_TIMEOUT_MS }))
}

export async function getReconciliations(adminId: string | null): Promise<Reconciliation[]> {
  if (!adminId) return []
  return request('/api/v1/admin/reconciliations', {}, { actorId: adminId })
}

export async function reconcileAttempt(adminId: string, attemptId: string): Promise<void> {
  await request(`/api/v1/admin/reconciliations/${attemptId}/classify`, { method: 'POST', headers: { 'Idempotency-Key': key() }, body: JSON.stringify({ action: 'RECONCILE' }) }, { actorId: adminId })
}

export function getAdminVideos(
  adminId: string,
  days: 1 | 7 | 30 | 90,
  offset = 0,
  search = '',
  brandId = '',
  health: AdminVideoHealthFilter = 'all',
  sort: AdminVideoSort = 'priority',
): Promise<AdminVideoPage> {
  const params = new URLSearchParams({
    days: String(days), offset: String(offset), limit: '24', health, sort,
  })
  if (search.trim()) params.set('search', search.trim())
  if (brandId) params.set('brand_id', brandId)
  return request(`/api/v1/admin/videos?${params.toString()}`, {}, { actorId: adminId })
}

export function getAdminVideoTasks(
  adminId: string, days: 1 | 7 | 30 | 90, offset = 0, search = '',
  health: AdminVideoHealthFilter = 'all', sort: AdminVideoSort = 'newest',
): Promise<AdminVideoTaskPage> {
  const params = new URLSearchParams({
    days: String(days), offset: String(offset), limit: '24', health, sort,
  })
  if (search.trim()) params.set('search', search.trim())
  return request(`/api/v1/admin/video-tasks?${params}`, {}, { actorId: adminId })
}

export function adminCutUrl(taskId: string, cut: AdminCutDeliverable, media: 'play' | 'thumbnail'): string {
  return `/api/v1/admin/video-tasks/${encodeURIComponent(taskId)}/cut/${cut.kind}/${encodeURIComponent(cut.batchId)}/${encodeURIComponent(cut.outputId)}/${media}`
}

export async function fetchAdminCutObjectUrl(adminId: string, taskId: string, cut: AdminCutDeliverable): Promise<string> {
  return URL.createObjectURL(await requestBlob(adminCutUrl(taskId, cut, 'play'), {
    actorId: adminId, timeoutMs: DOWNLOAD_TIMEOUT_MS,
  }))
}

export function getAdminVideoTrace(adminId: string, artifactId: string): Promise<AdminVideoTrace> {
  return request(`/api/v1/admin/videos/${encodeURIComponent(artifactId)}/trace`, {}, { actorId: adminId })
}

export async function fetchAdminReferenceObjectUrl(
  adminId: string,
  artifactId: string,
  assetId: string,
): Promise<string> {
  const path = adminVideoReferenceUrl(artifactId, assetId)
  return URL.createObjectURL(await requestBlob(path, {
    actorId: adminId, timeoutMs: DOWNLOAD_TIMEOUT_MS,
  }))
}

export function adminVideoReferenceUrl(artifactId: string, assetId: string): string {
  return `/api/v1/admin/videos/${encodeURIComponent(artifactId)}/references/${encodeURIComponent(assetId)}/content`
}

export function adminVideoThumbnailUrl(artifactId: string): string {
  return `/api/v1/admin/videos/${encodeURIComponent(artifactId)}/thumbnail`
}

export async function fetchAdminVideoObjectUrl(adminId: string, artifactId: string): Promise<string> {
  const path = `/api/v1/admin/videos/${encodeURIComponent(artifactId)}/play`
  return URL.createObjectURL(await requestBlob(path, { actorId: adminId, timeoutMs: DOWNLOAD_TIMEOUT_MS }))
}

export function getOperations(adminId: string): Promise<OperationsSnapshot> {
  return request('/api/v1/admin/operations', {}, { actorId: adminId })
}

export function getOperationsIssues(
  adminId: string,
  category: OperationsIssueCategory,
  offset = 0,
  limit = 20,
  search = '',
): Promise<OperationsIssuePage> {
  const params = new URLSearchParams({
    category, offset: String(offset), limit: String(limit),
  })
  if (search.trim()) params.set('search', search.trim())
  return request(`/api/v1/admin/operations/issues?${params.toString()}`, {}, { actorId: adminId })
}

export function getAdminUsers(adminId: string): Promise<AdminUser[]> {
  return request('/api/v1/admin/users', {}, { actorId: adminId })
}

export function createAdminUser(
  adminId: string,
  values: { username: string; password: string; brandId?: string | null; role?: AuthUser['role']; isBrandAdmin?: boolean },
): Promise<AdminUser> {
  return request('/api/v1/admin/users', {
    method: 'POST',
    body: JSON.stringify(values),
  }, { actorId: adminId })
}

export function resetAdminUserPassword(
  adminId: string,
  userId: string,
  password: string,
): Promise<void> {
  return request(`/api/v1/admin/users/${userId}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ password }),
  }, { actorId: adminId })
}

export function deactivateAdminUser(adminId: string, userId: string): Promise<void> {
  return request(`/api/v1/admin/users/${userId}`, {
    method: 'DELETE',
  }, { actorId: adminId })
}

export const getAccountBrands = (actorId: string) => request<AccountBrand[]>('/api/v1/admin/brands', {}, { actorId })
export const createAccountBrand = (actorId: string, name: string) => request<AccountBrand>('/api/v1/admin/brands', { method: 'POST', body: JSON.stringify({ name }) }, { actorId })
export const updateAccountPolicy = (actorId: string, userId: string, values: AccountPolicy) => request<AdminUser>(`/api/v1/admin/users/${userId}/policy`, { method: 'PATCH', body: JSON.stringify(values) }, { actorId })
export const getGlobalUsagePricing = (actorId: string) => request<GlobalUsagePricing>('/api/v1/admin/usage-pricing', {}, { actorId })
export const saveGlobalUsagePricing = (actorId: string, values: Omit<GlobalUsagePricing, 'legacyConflicts'>) => request<GlobalUsagePricing>('/api/v1/admin/usage-pricing', { method: 'PUT', body: JSON.stringify(values) }, { actorId })
export const getAccountUsage = (values: { userId?: string; brandId?: string; day?: string; startDate?: string; endDate?: string; status?: string; offset?: number; limit?: number; eventOffset?: number; eventLimit?: number; summaryOffset?: number; summaryLimit?: number; includeEvents?: boolean } = {}, actorId?: string) => {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== '') params.set(key, String(value))
  return request<AccountUsageReport>(`/api/v1/account/usage?${params}`, {}, { actorId })
}

export const getAccountEvents = (values: { userId?: string; brandId?: string; startDate?: string; endDate?: string; action?: string; offset?: number; limit?: number }, actorId?: string) => {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== '') params.set(key, String(value))
  return request<AccountEventReport>(`/api/v1/account/events?${params}`, {}, { actorId })
}

export type AdminUsageQuery = Parameters<typeof getAccountUsage>[0] & { search?: string; kind?: string; unpricedOnly?: boolean }
export const getAdminAccountUsage = (values: AdminUsageQuery, actorId: string) => {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values ?? {})) if (value !== undefined && value !== '') params.set(key, String(value))
  return request<AccountUsageReport>('/api/v1/admin/account-usage?' + params, {}, { actorId })
}

export function getAdminCredentials(adminId: string | null): Promise<AdminCredentialsStatus> {
  return adminId
    ? request('/api/v1/admin/credentials', {}, { actorId: adminId })
    : request('/api/v1/admin/credentials')
}

export function getLibTVAccountStatus(adminId: string): Promise<LibTVAccountStatus> {
  return request('/api/v1/admin/libtv-account', {}, { actorId: adminId })
}

export function getKlingCredits(adminId: string): Promise<import('./types').KlingCreditsStatus> {
  return request('/api/v1/admin/kling/credits', {}, { actorId: adminId })
}

export function getKlingBilling(adminId: string, limit = 100): Promise<import('./types').KlingBillingReport> {
  return request('/api/v1/admin/kling/billing?limit=' + limit, {}, { actorId: adminId })
}

export function getKlingStatus(adminId: string): Promise<KlingStatus> {
  return request('/api/v1/admin/kling/status', {}, { actorId: adminId })
}

export function getKlingConfig(adminId: string): Promise<KlingConfig> {
  return request('/api/v1/admin/kling/config', {}, { actorId: adminId })
}

export function updateKlingConfig(adminId: string, values: KlingConfigPatch): Promise<KlingConfig> {
  return request('/api/v1/admin/kling/config', {
    method: 'PATCH',
    body: JSON.stringify(values),
  }, { actorId: adminId })
}

export function getWaveSpeedConfig(adminId: string): Promise<WaveSpeedConfig> {
  return request('/api/v1/admin/wavespeed/config', {}, { actorId: adminId })
}

export function updateWaveSpeedConfig(adminId: string, values: WaveSpeedConfigPatch): Promise<WaveSpeedConfig> {
  return request('/api/v1/admin/wavespeed/config', {
    method: 'PATCH',
    body: JSON.stringify(values),
  }, { actorId: adminId })
}

export function getBachConfig(adminId: string): Promise<BachConfig> {
  return request('/api/v1/admin/bach/config', {}, { actorId: adminId })
}

export function updateBachConfig(adminId: string, values: BachConfigPatch): Promise<BachConfig> {
  return request('/api/v1/admin/bach/config', {
    method: 'PATCH',
    body: JSON.stringify(values),
  }, { actorId: adminId })
}

export function verifyWaveSpeedConfig(adminId: string): Promise<WaveSpeedConfig> {
  return request('/api/v1/admin/wavespeed/verify', {
    method: 'POST',
  }, { actorId: adminId, timeoutMs: LONG_RUNNING_TIMEOUT_MS })
}

export function createImageGenerationBatch(
  manifest: ImageGenerationClientManifest,
  files: File[],
): Promise<ImageGenerationBatch> {
  const body = new FormData()
  body.append('clientManifest', JSON.stringify(manifest))
  for (const file of files) body.append('files', file, file.name)
  return request('/api/v1/image-generation-batches', {
    method: 'POST',
    headers: { 'Idempotency-Key': manifest.clientBatchId },
    body,
  }, { timeoutMs: LONG_RUNNING_TIMEOUT_MS })
}

export function getImageGenerationBatch(batchId: string, signal?: AbortSignal): Promise<ImageGenerationBatch> {
  return request(`/api/v1/image-generation-batches/${encodeURIComponent(batchId)}`, { signal })
}

export function listImageGenerationBatches(offset = 0, signal?: AbortSignal): Promise<import('./types').ImageGenerationHistoryPage> {
  return request(`/api/v1/image-generation-batches?limit=20&offset=${offset}`, { signal })
}

export function setImageOutputQualityDecision(
  outputId: string,
  payload: {
    decision: 'QUALIFIED' | 'REJECTED'
    productFidelityPass: boolean
    humanCriticalPass: boolean
    humanReviewSeconds: number
    notes?: string | null
  },
): Promise<{
  id: string
  qualificationStatus: 'QUALIFIED' | 'REJECTED'
  qualityGates: Record<string, { verification?: string; outcome?: string }>
  reviewedAt: string
  humanReviewSeconds: number
}> {
  return request(`/api/v1/image-generation-outputs/${encodeURIComponent(outputId)}/quality-decision`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function refreshKlingCapabilities(adminId: string): Promise<KlingStatus> {
  return request('/api/v1/admin/kling/capabilities/refresh', {
    method: 'POST',
  }, { actorId: adminId })
}

export function loginKling(adminId: string): Promise<KlingStatus> {
  return request('/api/v1/admin/kling/login', {
    method: 'POST',
  }, { actorId: adminId })
}

export function logoutKling(adminId: string): Promise<KlingStatus> {
  return request('/api/v1/admin/kling/logout', {
    method: 'POST',
  }, { actorId: adminId })
}

export function getKlingElements(
  adminId: string | null,
  pageNum = 1,
  pageSize = 50,
  official = false,
  signal?: AbortSignal,
): Promise<KlingElementList> {
  const query = new URLSearchParams({
    pageNum: String(pageNum),
    pageSize: String(pageSize),
    official: String(official),
  })
  return request(`/api/v1/library/kling/elements?${query.toString()}`, { signal }, { actorId: adminId })
}

export function createKlingElement(adminId: string, values: KlingElementCreate): Promise<KlingElement> {
  return request('/api/v1/admin/kling/elements', {
    method: 'POST',
    body: JSON.stringify(values),
  }, { actorId: adminId, timeoutMs: LONG_RUNNING_TIMEOUT_MS })
}

export function deleteKlingElement(adminId: string | null, elementId: string): Promise<void> {
  return request(`/api/v1/admin/kling/elements/${encodeURIComponent(elementId)}`, {
    method: 'DELETE',
  }, { actorId: adminId, timeoutMs: LONG_RUNNING_TIMEOUT_MS })
}

export function startLibTVLogin(adminId: string): Promise<LibTVLoginStart> {
  return request('/api/v1/admin/libtv-login/start', {
    method: 'POST',
  }, { actorId: adminId })
}

export function switchLibTVAccount(adminId: string, account: string): Promise<LibTVAccountSwitch> {
  return request('/api/v1/admin/libtv-account/use', {
    method: 'POST',
    headers: { 'Idempotency-Key': key() },
    body: JSON.stringify({ account }),
  }, { actorId: adminId })
}

export function updateAdminCredentials(
  adminId: string | null,
  values: { libtvAccessKey?: string; modelApiKey?: string; modelBaseUrl?: string; clearModelBaseUrl?: boolean },
): Promise<AdminCredentialsStatus> {
  return request<AdminCredentialsStatus>('/api/v1/admin/credentials', {
    method: 'PATCH',
    body: JSON.stringify(values),
  }, { actorId: adminId })
}

export function setOperationalPause(
  adminId: string,
  expectedVersion: number,
  scope: 'GLOBAL' | 'PAID_SUBMISSIONS',
  paused: boolean,
  reason: string,
): Promise<{ version: number }> {
  return request('/api/v1/admin/pause', {
    method: 'POST',
    headers: { 'Idempotency-Key': key() },
    body: JSON.stringify({ expectedVersion, scope, paused, reason }),
  }, { actorId: adminId })
}

export function setPromptAutoConfirmation(
  adminId: string,
  expectedVersion: number,
  automatic: boolean,
): Promise<RuntimeConfigSnapshot> {
  return request('/api/v1/admin/prompt-confirmation', {
    method: 'POST',
    body: JSON.stringify({ expectedVersion, automatic }),
  }, { actorId: adminId })
}

export interface CutflowHandoff {
  editMode?: 'automatic' | 'manual'
  id: string
  handoffKey: string
  sourceArtifactId: string
  status: string
  createdAt?: string
  updatedAt?: string
  cutflowBatchId: string | null
  cutflowEditorUrl: string | null
  errorMessage: string | null
  errorCode?: string | null
  outputs: Array<{ id: string; sha256: string; reviewStatus: string; durationSeconds: number; createdAt: string; mediaUrl: string }>
}
export interface CutflowDiagnostics {
  checkedAt: string
  handoffKey: string
  batchId: string | null
  localStatus: string
  sender: { state: 'online' | 'stale' | 'unknown'; heartbeatAt: string | null }
  lastContactAt: string | null
  lastProgressAt: string | null
  remoteStage: string | null
  diagnosticsState: 'available' | 'stale' | 'unavailable'
  workerAlive: boolean | null
  scopeMatches: boolean | null
  retryCount: number
  nextRetryAt: string | null
  errorCode: string | null
}
export const getCutflowDiagnostics = (id: string, signal?: AbortSignal) => request<CutflowDiagnostics>(`/api/v1/cutflow-handoffs/${encodeURIComponent(id)}/diagnostics`, { signal })
export const listCutflowHandoffs = (variantId: string, signal?: AbortSignal) => request<{ enabled: boolean; siteRequired?: boolean; handoffs: CutflowHandoff[] }>(`/api/v1/cutflow-handoffs?variant_id=${encodeURIComponent(variantId)}`, { signal })
export const createCutflowHandoff = (taskId: string, variantId: string) => request<CutflowHandoff>('/api/v1/cutflow-handoffs', { method: 'POST', body: JSON.stringify({ taskId, variantId }) })
export interface MultiCutflowTask {
  id: string
  status: string
  errorCode: string | null
  errorMessage: string | null
  createdAt: string
  updatedAt: string
  sources: Array<{ clipId: string; order: number; taskId: string; variantId: string; artifactId: string; title: string; durationSeconds: number }>
  outputs: Array<{ id: string; sha256: string; reviewStatus: string; durationSeconds: number; width: number; height: number; createdAt: string; mediaUrl: string; viewedAt?: string | null }>
}
export interface AutoCutflowDispatchStatus {
  taskId: string
  state: 'WAITING_DISPATCH' | 'MANUAL' | 'RECORDED' | 'WAITING_ORIGINALS' | 'BLOCKED' | 'WORKER_UNAVAILABLE'
  reasonCode: string | null
  message: string | null
  worker: { state: 'online' | 'stale' | 'unknown'; heartbeatAt: string | null }
  batchId: string | null
  batchStatus: string | null
  requiredUsd: number | null
  remainingUsd: number | null
}

export const getAutoCutflowDispatchStatus = (taskId: string, signal?: AbortSignal) =>
  request<AutoCutflowDispatchStatus>(`/api/v1/cutflow-handoffs/auto-dispatch/${encodeURIComponent(taskId)}`, { signal })

export const listMultiCutflowTasks = (signal?: AbortSignal) => request<{ tasks: MultiCutflowTask[] }>('/api/v1/cutflow-handoffs/multi', { signal })
export const createMultiCutflowTask = (sourceIds: string[], idempotencyKey: string, confirmPolicy: boolean, allowSingleSource = false) => request<MultiCutflowTask>('/api/v1/cutflow-handoffs/multi', { method: 'POST', body: JSON.stringify({ sourceIds, idempotencyKey, confirmPolicy, allowSingleSource }) })
export const markMultiCutflowOutputViewed = (batchId: string, outputId: string) => request<{ viewedAt: string }>(`/api/v1/cutflow-handoffs/multi/${encodeURIComponent(batchId)}/outputs/${encodeURIComponent(outputId)}/viewed`, { method: 'POST' })
export const retryMultiCutflowTask = (id: string) => request<MultiCutflowTask>(`/api/v1/cutflow-handoffs/multi/${encodeURIComponent(id)}/retry`, { method: 'POST' })
export const retryCutflowHandoff = (id: string) => request<CutflowHandoff>(`/api/v1/cutflow-handoffs/${encodeURIComponent(id)}/retry`, { method: 'POST' })
export type CutflowSiteChoices = { defaultProfileId: string | null; sites: Array<{ id: string; version: string; name: string; siteUrl: string }> }
export const getCutflowSiteChoices = (signal?: AbortSignal) => request<CutflowSiteChoices>('/api/v1/cutflow-handoffs/sites', { signal })
export const bindCutflowSourceSite = (taskId: string, siteProfileId: string) => request('/api/v1/cutflow-handoffs/source-site', { method: 'POST', body: JSON.stringify({ taskId, siteProfileId }) })
