import { describe, expect, it } from 'vitest'
import { ApiError } from './api'
import { errorMessage, plainText, userFacingError, validationMessage } from './errorPresentation'

describe('plain-language errors', () => {
  it('explains the classic new-task maintenance freeze without implying existing tasks stopped', () => {
    const message = userFacingError({ code: 'CLASSIC_NEW_VIDEO_TASKS_PAUSED', status: 503 })
    expect(message).toContain('暂停新建视频任务')
    expect(message).toContain('已提交任务')
  })

  it('explains how to recover from login, permissions, budget and unavailable generation', () => {
    expect(userFacingError({ code: 'CSRF_REJECTED' })).toContain('刷新页面')
    expect(userFacingError({ code: 'PAGE_ACCESS_DENIED' })).toContain('联系管理员')
    expect(userFacingError({ code: 'USD_DAILY_BUDGET_EXCEEDED' })).toContain('明天再试')
    expect(plainText('本站尚未授权真实付费生成')).toContain('联系管理员')
    expect(plainText('执行器未在线')).toContain('后台生成程序')
  })

  it.each(['mayHaveBeenAccepted', 'mayHaveBeenCharged', 'requiresReconciliation'] as const)(
    'prioritizes uncertain paid submission when %s is set', (flag) => {
      const error = new ApiError(503, 'WAVESPEED_SERVICE_UNAVAILABLE', 'retry now', { diagnostic: true }, true,
        flag === 'mayHaveBeenAccepted', flag === 'mayHaveBeenCharged', flag === 'requiresReconciliation', 'support-id')
      expect(error.message).toContain('确认前不要重新生成')
      expect(errorMessage(error, '提交失败')).toBe(error.message)
      expect(error.code).toBe('WAVESPEED_SERVICE_UNAVAILABLE')
      expect(error.rawMessage).toBe('retry now')
      expect(error.retryable).toBe(true)
      expect(error.details).toEqual({ diagnostic: true })
      expect(error.correlationId).toBe('support-id')
      expect(error[flag]).toBe(true)
    },
  )

  it('separates historical point-limit errors from current USD budget rejections', () => {
    for (const code of ['POINTS_LIMIT_EXCEEDED', 'IMAGE_DAILY_LIMIT_EXCEEDED', 'VIDEO_QUOTA_REJECTED']) {
      const message = userFacingError({ code })
      expect(message).toContain('历史任务')
      expect(message).toContain('已取消')
    }
    const usdMessage = userFacingError({ code: 'USD_DAILY_BUDGET_EXCEEDED' })
    expect(usdMessage).toContain('预算')
    expect(usdMessage).not.toContain('历史任务')
  })

  it('hides raw English exceptions, unrecognized codes, paths and provider payloads', () => {
    for (const raw of ['Traceback: ValueError', 'NEW_PROVIDER_FAILURE', '生成失败：request_id=test', '后台返回 {"token":"private"}', '失败 https://provider.example/private']) {
      expect(plainText(raw)).not.toContain(raw)
    }
    expect(errorMessage(new Error('Unexpected token <'), '任务列表暂时无法读取，请刷新页面。')).toBe('任务列表暂时无法读取，请刷新页面。')
    expect(plainText('最多上传 7 张图片，请减少后再试。')).toContain('7 张')
  })

  it('translates validation locations and constraints without exposing input values', () => {
    const detail = [
      { loc: ['body', 'password'], type: 'string_too_short', ctx: { min_length: 8 }, input: 'secret' },
      { loc: ['body', 'duration'], type: 'less_than_equal', ctx: { le: 15 } },
      { loc: ['body', 'private_field'], msg: 'Value error, secret=private' },
    ]
    expect(validationMessage(detail)).toBe('密码至少需要 8 个字符。视频时长不能大于 15。')
    expect(new ApiError(422, 'VALIDATION_FAILED', validationMessage(detail)).message).toBe(validationMessage(detail))
    expect(validationMessage([{}])).toContain('必填项')
  })

  it('distinguishes upstream AI gateway failure from reference-grounding errors', () => {
    const gateway = userFacingError({ code: 'AI_CREATIVE_GATEWAY_TIMEOUT', status: 504 })
    expect(gateway).toContain('HTTP 504')
    expect(gateway).toContain('网关')
    expect(gateway).not.toContain('参考图片清晰')
    expect(userFacingError({ code: 'AI_CREATIVE_CONNECTION_FAILED', status: 502 })).toContain('Base URL')
    expect(userFacingError({ code: 'AI_CREATIVE_AUTH_FAILED', status: 502 })).toContain('密钥')
    expect(userFacingError({ code: 'AI_CREATIVE_REQUEST_REJECTED', status: 502 })).toContain('HTTP 400/422')
    expect(userFacingError({ code: 'AI_CREATIVE_PLAN_INVALID', status: 422 })).toContain('本地参考图或结构校验')
    expect(userFacingError({ code: 'AI_CREATIVE_PLAN_INVALID', status: 422 })).not.toContain('自动修正一次')
    expect(userFacingError({ code: 'AI_CREATIVE_COMPILE_FAILED', status: 422 })).toContain('请求格式')
    expect(userFacingError({ code: 'AI_CREATIVE_NOT_CONFIGURED', status: 422 })).toContain('配置')
  })

  it('explains the known Kling 4.0 audio schema rejection without blaming images', () => {
    const message = userFacingError({
      code: 'KLING_API_VALIDATION_FAILED',
      message: "audio mode 'off' is not supported by the current model",
    })
    expect(message).toContain('HTTP 400 / 1201')
    expect(message).toContain('音频参数')
    expect(message).toContain('无需更换图片')
    expect(userFacingError({
      code: 'KLING_API_VALIDATION_FAILED',
      message: 'Some other input was invalid',
    })).not.toContain('音频参数')
    expect(userFacingError({
      code: 'KLING_API_VALIDATION_FAILED',
      message: "audio mode 'off' is not supported by the current model",
      mayHaveBeenCharged: true,
    })).toContain('核对任务')
  })

  it('preserves specific file validation while separating internal processing errors', () => {
    expect(userFacingError({ code: 'IMAGE_DIMENSIONS_INVALID', message: '图片尺寸为 100 × 640 像素，宽、高均须在 128～8192 像素之间。' })).toContain('100 × 640')
    expect(userFacingError({ code: 'IMAGE_TYPE_UNSUPPORTED', message: '图片实际格式为 GIF，本站只支持 JPG、PNG、WebP。' })).toContain('GIF')
    expect(userFacingError({ code: 'UPLOAD_PROCESSING_FAILED', message: 'secret=private ValueError' })).toContain('尚不能认定图片不合格')
    expect(userFacingError({ code: 'UPLOAD_PROCESSING_FAILED', message: 'secret=private ValueError' })).not.toContain('private')
  })

  it('keeps download failures distinct from generation failures', () => {
    expect(userFacingError({ code: 'DOWNLOAD_RETRYABLE' })).toContain('无需重新生成')
    expect(userFacingError({ status: 410 })).toContain('找回已生成的视频')
  })
})
