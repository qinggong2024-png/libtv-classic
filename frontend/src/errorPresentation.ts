/** User-facing copy only. Keep API codes, payment flags and diagnostic data intact. */
type ErrorContext = {
  code?: string | null
  message?: string | null
  status?: number
  mayHaveBeenAccepted?: boolean
  mayHaveBeenCharged?: boolean
  requiresReconciliation?: boolean
}

const messages: Record<string, string> = {}
function register(codes: string, message: string) {
  for (const code of codes.split(' ')) messages[code] = message
}

register('CLASSIC_NEW_VIDEO_TASKS_PAUSED', '系统正在安全维护，暂时暂停新建视频任务。已提交任务、下载和剪辑不受影响，请稍后再试。')

register('AUTH_REQUIRED UNAUTHENTICATED SESSION_EXPIRED', '登录已过期，请重新登录后继续。')
register('INVALID_CREDENTIALS LOGIN_FAILED AUTHENTICATION_FAILED', '账号或密码不正确，请检查后重新登录。')
register('ACCOUNT_DISABLED ACCOUNT_INACTIVE', '这个账号已停用，请联系管理员。')
register('CSRF_REJECTED', '页面的安全验证已过期，请刷新页面后再试；仍有问题时请重新登录。')
register('PERMISSION_DENIED PAGE_ACCESS_DENIED FORBIDDEN', '当前账号没有这项操作的权限，请联系管理员。')
register('CURRENT_PASSWORD_INVALID', '当前密码不正确，请检查后重新输入。')
register('PASSWORD_UNCHANGED', '新密码不能和当前密码相同，请换一个密码。')
register('USERNAME_EXISTS', '这个账号名已被使用，请换一个。')
register('PROTECTED_ACCOUNT', '这个管理员账号受到保护，不能执行此操作。')
register('VERSION_CONFLICT PAUSE_STATE_CONFLICT PROMPT_CONFIRMATION_STATE_CONFLICT', '内容刚刚有更新，请刷新页面，确认最新内容后再操作。')
register('MAINTENANCE_QUERY_ONLY', '网站正在维护，暂时不能生成。你仍可以登录和查看已有任务，请稍后再来。')
register('GLOBAL_PAUSED', '管理员已暂停新任务，请联系管理员恢复后再生成。')
register('PAID_SUBMISSIONS_PAUSED', '管理员已暂停新的付费生成，请联系管理员恢复后再提交。')
register('PAID_GENERATION_BLOCKED', '本站尚未开启付费生成，请联系管理员确认是否开通。')
register('USD_DAILY_BUDGET_EXCEEDED', '今天的生成预算已用完，请明天再试，或联系管理员调整预算。')
register('IMAGE_DAILY_LIMIT_EXCEEDED POINTS_LIMIT_EXCEEDED VIDEO_QUOTA_REJECTED', '这是历史任务的旧点数或数量限额提示，相关限制已取消。请创建新任务；提高美元预算不会自动恢复历史失败任务。')
register('USD_PRICE_REQUIRED', '这个模型还没有设置价格，暂时不能生成，请联系管理员补充价格。')
register('RATE_LIMITED KLING_RATE_LIMITED KLING_API_RATE_LIMITED WAVESPEED_RATE_LIMITED', '生成服务现在比较繁忙，请稍等一会儿再试。')
register('KLING_INSUFFICIENT_CREDITS KLING_API_BALANCE_INSUFFICIENT WAVESPEED_BALANCE_INSUFFICIENT', 'API 余额不足，请联系管理员充值。')
register('KLING_API_ACCOUNT_UNAVAILABLE', '生成服务的账号暂不可用，请联系管理员检查。')
register('KLING_API_KEY_MISSING WAVESPEED_NOT_CONFIGURED', '还没有设置好生成服务，请联系管理员填写服务密钥并检查连接。')
register('KLING_API_AUTHENTICATION_FAILED KLING_CLI_NOT_AUTHENTICATED LIBTV_AUTH_REQUIRED WAVESPEED_AUTHENTICATION_FAILED', '生成服务的登录或密钥已失效，请联系管理员重新设置并检查连接。')
register('KLING_API_VERIFICATION_FAILED KLING_API_SERVER_ERROR KLING_API_RESPONSE_ERROR WAVESPEED_SERVICE_UNAVAILABLE WAVESPEED_RESPONSE_ERROR WAVESPEED_RESPONSE_INVALID', '生成服务暂时没有正常回应，请稍后查看任务状态；仍有问题时请联系管理员。')
register('KLING_API_VALIDATION_FAILED WAVESPEED_VALIDATION_FAILED', '生成服务无法使用当前素材或设置，请检查图片、提示词和生成选项。')
register('KLING_REMOTE_TASK_FAILED IMAGE_GENERATION_FAILED GENERATION_TERMINAL_FAILED', '这次没有生成成功，请检查素材和设置；如果仍然失败，请联系管理员。')
register('KLING_PRIOR_RISK_CONTROL_REJECTED', '同一组商品参考图近期至少两次被可灵审核拒绝，且没有成功回执。已暂停本次自动付费提交；请先核对参考图和商品印花，必要时联系管理员处理。')
register('KLING_REMOTE_RISK_CONTROL_REJECTED', '可灵已受理任务，但云端内容风控审核未通过。服务未说明具体触发素材或词句；请先核对素材和提示词，暂勿直接重试，扣费以可灵回执为准。')
register('WRITE_UNCERTAIN WAVESPEED_SUBMISSION_UNCERTAIN PAID_STATE_UNCERTAIN RECONCILE_BEFORE_RETRY KLING_REMOTE_STATUS_UNKNOWN', '暂时无法确认任务是否已提交或扣费。请先核对任务状态，确认前不要重新生成，以免重复扣费。')
register('IDEMPOTENCY_REQUEST_IN_PROGRESS', '上一次提交还在处理中，请先查看任务记录，不要重复提交。')
register('IDEMPOTENCY_CONFLICT IDEMPOTENCY_KEY_REUSED', '这次提交与之前的记录不一致，请先查看已有任务，确认前不要重新生成。')
register('ANALYSIS_TIMEOUT ANALYSIS_REQUEST_TIMED_OUT PROMPT_ASSISTANT_TIMEOUT', '分析等待时间太长，暂时没有拿到结果。请稍后重试分析。')
register('ANALYSIS_CANCELLED ANALYSIS_STALE_RECOVERED ANALYSIS_REQUEST_CANCELLED', '上一次分析中断了，素材仍保留，可以重新分析。')
register('ANALYSIS_PROVIDER_FAILED ANALYSIS_SCHEMA_INVALID ANALYSIS_REQUEST_FAILED ANALYSIS_REQUEST_REJECTED PROMPT_ASSISTANT_FAILED', '这次没能得到完整的分析结果，请检查素材后重新分析；仍失败时请联系管理员。')
register('CONFIRMATION_CANCELLED CONFIRMATION_BACKGROUND_FAILED CONFIRMATION_PROCESS_RESTARTED', '视频方案没有确认完成，请查看任务的最新状态后再操作。')
register('PROMPT_LOCKED', '提示词已经确认，当前不能修改。需要调整时请另建任务。')
register('CUSTOM_PROMPT_TOO_LONG COMPILED_PROMPT_TOO_LONG', '提示词太长，请删减一些内容后再提交。')
register('AI_CREATIVE_INTERRUPTED', 'AI 编排任务因后台服务中断而未能完成。请联系管理员核对原任务的生成提交记录后恢复，暂不建议重复创建任务。')
register('AI_CREATIVE_RETRY_SCHEDULED', 'AI 编排遇到临时网络错误、超时或限流，已安排后台有限次数自动重试，不需要重复提交，本次未提交可灵付费生成。')
register('AI_CREATIVE_PLAN_UNAVAILABLE', 'AI创意编排未能完成，可能是编排服务未配置或响应异常。本次未提交视频生成，请检查AI配置后重试。')
register('AI_CREATIVE_NOT_CONFIGURED', '经典版未正确配置AI编排服务。请检查模型、Base URL及API密钥，本次未提交视频生成。')
register('AI_CREATIVE_PLAN_INVALID', 'AI已返回创意方案，但未通过本地参考图或结构校验。系统已记录具体校验规则，建议联系管理员排查；本次未提交视频生成。')
register('AI_CREATIVE_COMPILE_FAILED', 'AI编排已返回，但结果无法转换为当前生成服务支持的请求格式。本次未提交视频生成；请联系管理员检查接口结构或提示词长度。')
register('AI_CREATIVE_GATEWAY_TIMEOUT', 'AI编排服务的上游网关返回 HTTP 504 超时，未拿到编排结果。这不是参考图校验错误，请检查大模型API代理的运行状态后重试。本次未提交视频生成。')
register('AI_CREATIVE_REQUEST_TIMEOUT', '等待AI编排接口响应超时，未拿到编排结果。请检查大模型API连接、响应速度或代理服务后重试。本次未提交视频生成。')
register('AI_CREATIVE_CONNECTION_FAILED', '无法连接大模型AI编排接口，请检查Base URL、DNS、网络或代理服务是否可达。本次未提交视频生成。')
register('AI_CREATIVE_AUTH_FAILED', '大模型AI编排接口返回鉴权或权限错误（HTTP 401/403），请检查API密钥和模型权限。本次未提交视频生成。')
register('AI_CREATIVE_RATE_LIMITED', '大模型AI编排接口返回限流（HTTP 429），请稍后重试或检查接口配额。本次未提交视频生成。')
register('AI_CREATIVE_ENDPOINT_NOT_FOUND', '大模型AI编排接口返回 HTTP 404，请检查Base URL的API路径和所选模型是否存在。本次未提交视频生成。')
register('AI_CREATIVE_REQUEST_REJECTED', '大模型AI编排接口拒绝了请求（HTTP 400/422），可能是模型、请求格式或结构化输出不兼容，并不能据此认定参考图有问题。本次未提交视频生成。')
register('AI_CREATIVE_UPSTREAM_UNAVAILABLE', 'AI编排上游服务返回服务器错误（HTTP 5xx），请检查代理或上游模型服务状态。本次未提交视频生成。')
register('AI_CREATIVE_RESPONSE_INVALID', 'AI编排接口返回的数据无法解析为有效方案，可能是结构化输出不兼容；请检查模型接口配置后重试。本次未提交视频生成。')
register('INPUT_TOO_LARGE WAVESPEED_FILE_TOO_LARGE', '文件太大，请压缩文件或缩短视频后再上传。')
register('IMAGE_TYPE_UNSUPPORTED IMAGE_TYPE_MISMATCH IMAGE_INVALID', '这张图片无法读取，请换成能正常打开的 JPG、PNG 或 WebP 图片。')
register('INPUT_INVALID IMAGE_BATCH_INVALID VALIDATION_FAILED REQUEST_VALIDATION_FAILED', '有些内容没有填写完整或格式不对，请检查必填项、素材和生成设置。')
register('IMAGE_EMPTY', '图片文件为空，请重新选择原图。')
register('IMAGE_DIMENSIONS_INVALID', '图片宽、高均须在 128～8192 像素之间。')
register('UPLOAD_PROCESSING_FAILED', '网站处理上传文件时发生内部错误，尚不能认定图片不合格，请联系管理员。')
register('DOWNLOAD_ALREADY_IN_PROGRESS', '成品正在下载，请稍候，不需要重复点击。')
register('DOWNLOAD_RETRYABLE WAVESPEED_OUTPUT_DOWNLOAD_FAILED', '成品暂时没有下载成功，请稍后再次下载，无需重新生成。')
register('DOWNLOAD_SOURCE_MISSING', '暂时找不到成品文件，请联系管理员检查能否找回，无需立即重新生成。')
register('MATERIALIZATION_INTERNAL_ERROR MATERIALIZATION_INTERRUPTED', '素材准备没有完成，请稍后查看任务；仍未继续时请联系管理员。')
register('DATABASE_SCHEMA_OUTDATED DATABASE_UNAVAILABLE INTERNAL_ERROR', '网站暂时无法完成这项操作，请稍后再试；仍有问题时请联系管理员。')
register('MODEL_SCHEMA_INCOMPATIBLE', '当前模型暂不支持这些设置，请换一个模型，或联系管理员检查。')
register('FIXED_VIDEO_PREFLIGHT_BLOCKED', '开始生成前的检查没有通过，请检查素材和生成设置，或联系管理员。')
register('TECHNICAL_HARD_GATE_NOT_PASSED IMAGE_TECHNICAL_HARD_GATE_NOT_PASSED', '素材还没有通过生成前的检查，请检查图片是否清晰、完整。')
register('CONSISTENCY_REVIEW_REQUIRED VALIDATION_MANUAL_REVIEW MANUAL_REVIEW FINAL_APPROVAL_REJECTED', '结果需要人工检查，请联系管理员确认后再继续。')
register('FEEDBACK_NOT_READY', '成品还没有准备好，请等生成完成后再提交反馈。')
register('VARIANT_NOT_FOUND NOT_FOUND', '没有找到这条记录，它可能已被删除，或当前账号无法查看。请刷新列表。')
register('CURSOR_INVALID', '列表已经更新，请刷新页面重新查看。')
register('PREPARE_ONLY_COMPLETE PREPARE_ONLY_TASK', '素材已经准备好，这个任务只做准备，不会开始付费生成。')
register('BACH_ELEMENTS_NOT_IN_P1', 'Bach 目前只支持首帧或首尾帧，不能单独添加产品参考图。本次尚未提交生成，请移除产品参考图后再试。')

const replacements: [RegExp, string][] = [
  [/API Key|API key|Access Token/g, '服务密钥'],
  [/Provider/g, '生成服务'],
  [/鉴权/g, '身份验证'],
  [/凭据/g, '连接信息'],
  [/预检/g, '生成前检查'],
  [/幂等键/g, '提交凭证'],
  [/对账/g, '核对任务和扣费情况'],
  [/远端/g, '生成服务中的'],
  [/轮询/g, '自动查询'],
  [/执行器|运行器/g, '后台生成程序'],
  [/心跳/g, '在线状态'],
  [/API/g, '服务'],
]

/** Preserve useful plain Chinese validation copy; never display raw exceptions or codes. */
export function plainText(value: string | null | undefined, fallback = '暂时无法完成，请稍后再试；仍有问题时请联系管理员。'): string {
  if (!value?.trim()) return fallback
  const text = value.trim()
  if (messages[text]) return messages[text]
  if (/尚未授权.*(?:付费|真实)|未授权真实付费/.test(text)) return messages.PAID_GENERATION_BLOCKED
  if (/runner|执行器|工作节点/.test(text) && /未|没有|离线|不可用/.test(text)) return '后台生成程序还没有准备好，请联系管理员启动后再试。'
  if (/Failed to fetch|NetworkError|Load failed|network request failed/i.test(text)) return '暂时连不上网站，请检查网络后刷新页面。若刚提交过生成任务，请先查看任务记录，避免重复提交。'
  if (!/[\u3400-\u9fff]/.test(text) || /[A-Z][A-Z0-9]+_[A-Z0-9_]+|\b[a-z]+_[a-z_]+\b|Traceback|Exception|https?:\/\/|\b(?:GET|POST|PATCH)\b|[{}]|[A-Z]:\\/i.test(text)) return fallback
  return replacements.reduce((result, [pattern, replacement]) => result.replace(pattern, replacement), text)
}

export function userFacingError(context: ErrorContext): string {
  const { code, message, status } = context
  // An ambiguous paid write takes precedence over a reassuring but incomplete provider message.
  if (context.mayHaveBeenAccepted || context.mayHaveBeenCharged || context.requiresReconciliation) return messages.WRITE_UNCERTAIN
  if (code === 'KLING_API_ACCOUNT_UNAVAILABLE' &&
    /(?:balance|credits?|余额|灵感值).*(?:not enough|insufficient|exhausted|不足|用完)/i.test(message ?? '')) {
    return messages.KLING_API_BALANCE_INSUFFICIENT
  }
  // Historic failed tasks used a generic code; identify only Kling's exact risk-control response.
  if (code === 'KLING_REMOTE_TASK_FAILED' && /failure to pass the risk control system/i.test(message ?? '')) {
    return messages.KLING_REMOTE_RISK_CONTROL_REJECTED
  }
  // Known Kling 4.0 schema mismatch: show the cause, not a misleading image-quality warning.
  if (code === 'KLING_API_VALIDATION_FAILED' && /audio mode ['"]?off['"]? is not supported by the current model/i.test(message ?? '')) {
    return '可灵 4.0 接口拒绝了旧版音频参数（HTTP 400 / 1201），本次尚未开始生成。请管理员更新接口适配，无需更换图片或提示词。'
  }
  if (code === 'VALIDATION_FAILED' || code === 'REQUEST_VALIDATION_FAILED') return plainText(message, messages.VALIDATION_FAILED)
  if (code && ['IMAGE_EMPTY', 'IMAGE_DIMENSIONS_INVALID', 'IMAGE_TYPE_UNSUPPORTED', 'IMAGE_INVALID', 'INPUT_TOO_LARGE'].includes(code)) return plainText(message, messages[code])
  if (code && messages[code]) return messages[code]
  if (status === 401) return '登录信息不正确或已过期，请检查账号和密码后重新登录。'
  if (status === 403) return messages.PERMISSION_DENIED
  if (status === 404) return messages.NOT_FOUND
  if (status === 410) return '文件已经过期或无法直接下载，请尝试找回已生成的视频，或联系管理员检查。'
  if (status === 413) return messages.INPUT_TOO_LARGE
  if (status === 429) return messages.RATE_LIMITED
  if (status && status >= 500) return '网站或生成服务暂时没有正常回应，请稍后再试。若刚提交过生成任务，请先查看任务记录。'
  return plainText(message)
}

export function errorMessage(cause: unknown, fallback: string): string {
  if (cause && typeof cause === 'object') {
    const context = cause as ErrorContext
    if (!context.code && !context.status) return plainText(context.message, fallback)
    return userFacingError({ ...context, message: typeof context.message === 'string' ? context.message : fallback })
  }
  return fallback
}

const fieldLabels: Record<string, string> = {
  'Idempotency-Key': '上传请求凭证', 'X-Source-Image-Output-Id': '来源图片标识',
  username: '账号', password: '密码', currentPassword: '当前密码', newPassword: '新密码',
  title: '任务名称', prompt: '提示词', customPrompt: '自定义提示词', file: '上传文件',
  durationSeconds: '视频时长', duration: '视频时长', aspectRatio: '画面比例', resultCount: '生成数量',
  storyboardUploadIds: '分镜图片', referenceVideoUploadId: '参考视频', subjectImages: '主体图片',
  apiKey: '服务密钥', baseUrl: '服务地址', maxSubmitConcurrency: '同时提交的任务数', maxPollConcurrency: '同时查询的任务数',
}

export function validationMessage(details: unknown[]): string {
  const copy = details.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const entry = item as { loc?: unknown[]; type?: string; ctx?: { min_length?: number; max_length?: number; ge?: number; le?: number } }
    const field = entry.loc?.map(String).map((part) => fieldLabels[part]).find(Boolean)
    if (!field) return []
    if (entry.type === 'missing') return [`请填写${field}。`]
    if (entry.type === 'string_too_short' && entry.ctx?.min_length != null) return [`${field}至少需要 ${entry.ctx.min_length} 个字符。`]
    if (entry.type === 'string_too_long' && entry.ctx?.max_length != null) return [`${field}最多填写 ${entry.ctx.max_length} 个字符。`]
    if (entry.type === 'greater_than_equal' && entry.ctx?.ge != null) return [`${field}不能小于 ${entry.ctx.ge}。`]
    if (entry.type === 'less_than_equal' && entry.ctx?.le != null) return [`${field}不能大于 ${entry.ctx.le}。`]
    return [`请检查${field}的填写内容和格式。`]
  })
  return [...new Set(copy)].join('') || messages.VALIDATION_FAILED
}

register('DURABLE_CONFIRM_REVIEW', '生成准备结果待核对，已保留原任务，请勿重复提交。')

register('DURABLE_EXECUTION_UNAVAILABLE', '后台执行器暂未就绪，当前操作未被接收。原有任务不受影响，请稍后重试。')
