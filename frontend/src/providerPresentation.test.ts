import { describe, expect, it } from 'vitest'

import {
  generationProviderKind,
  deepVideoProgressLabel,
  deepVideoStageLabel,
  deepVideoTaskBadgeLabel,
  providerTaskBadgeLabel,
  videoModelDisplayName,
  providerActionRequiredMessage,
  providerProgress,
  providerTimingStageLabel,
  variantExecutionMessage,
} from './providerPresentation'

describe('provider presentation', () => {
  it('normalizes the configured Kling provider without treating it as LibTV', () => {
    expect(generationProviderKind('kling')).toBe('kling')
    expect(generationProviderKind('kling_official_cli')).toBe('kling')
    expect(generationProviderKind('kling_official_api')).toBe('kling')
    expect(generationProviderKind('bach')).toBe('bach')
    expect(generationProviderKind('libtv')).toBe('legacy')
  })

  it('uses provider-specific queue and generation copy', () => {
    expect(providerProgress('QUEUED', 'kling').label).toContain('可灵官方服务')
    expect(providerProgress('QUEUED', 'kling_official_api').label).toContain('可灵官方服务')
    expect(providerProgress('QUEUED', 'libtv').label).toContain('历史生成通道')
    expect(providerProgress('QUEUED', 'bach').label).toContain('Bach 图片转视频')
    expect(providerTimingStageLabel('MATERIALIZATION', 'kling')).toBe(
      '可灵官方服务 素材准备',
    )
    expect(providerTimingStageLabel('MATERIALIZATION', 'libtv')).toBe('历史任务素材准备')
  })

  it('keeps Seedance and Depth Anything visibly separate from Kling provider labels', () => {
    const seedance = 'bytedance/seedance-2.5-flash/video-edit'
    const depth = 'wavespeed-ai/depth-anything/video'
    expect(videoModelDisplayName(seedance)).toBe('Seedance 2.5 Flash')
    expect(videoModelDisplayName(depth)).toBe('Depth Anything')
    expect(deepVideoTaskBadgeLabel('recreate', seedance)).toBe('深度视频 · Seedance 2.5 Flash')
    expect(deepVideoTaskBadgeLabel('depth', null)).toBe('深度视频 · Depth Anything')
    expect(providerTaskBadgeLabel('wavespeed_video', 'DEEP_VIDEO', seedance, 'recreate')).toBe('深度视频 · Seedance 2.5 Flash')
    expect(providerTaskBadgeLabel('wavespeed_video', 'FIXED_TEMPLATE', 'kwaivgi/kling-video-o3-pro/reference-to-video')).toBe('可灵 · Kling O3')
    expect(deepVideoStageLabel('REPLACEMENT', seedance)).toBe('Seedance 2.5 Flash 人物与商品替换')
    expect(deepVideoStageLabel('DEPTH_EXTRACTION', seedance, depth)).toBe('Depth Anything 深度提取')
    expect(deepVideoProgressLabel('GENERATING', 'recreate', seedance)).toContain('Seedance 2.5 Flash')
    expect(deepVideoProgressLabel('GENERATING', 'recreate', seedance, depth, 'depth', 'depth-first-v1')).toContain('Depth Anything 深度提取')
    expect(providerProgress('GENERATING', 'kling_official_api').label).toContain('可灵官方服务')
  })

  it('explains queued capacity instead of recommending duplicate submissions', () => {
    const message = variantExecutionMessage({
      generationStatus: 'PENDING',
      downloadStatus: 'NOT_READY',
      downloadArtifactId: null,
      errorCode: 'KLING_API_RATE_LIMITED',
      providerStatus: 'waiting_capacity',
      provider: 'kling_official_api',
    })
    expect(message).toBe('等待开始生成')
  })

  it('keeps balance shortage visible during automatic recovery', () => {
    const message = variantExecutionMessage({
      generationStatus: 'PENDING',
      downloadStatus: 'NOT_READY',
      downloadArtifactId: null,
      errorCode: 'KLING_API_BALANCE_INSUFFICIENT',
      providerStatus: 'waiting_balance',
      provider: 'kling_official_api',
    })
    expect(message).toContain('API 余额不足，请联系管理员充值')
    expect(message).toContain('自动恢复')
  })

  it('shows distinct uncertain-state recovery boundaries', () => {
    const base = {
      generationStatus: 'UNKNOWN',
      downloadStatus: 'PENDING',
      downloadArtifactId: null,
      errorCode: null,
    }
    expect(variantExecutionMessage({ ...base, provider: 'kling_official_cli' })).toContain('核对任务和扣费情况')
    expect(variantExecutionMessage({ ...base, provider: 'kling_official_api' })).toContain(
      '可灵',
    )
    expect(variantExecutionMessage({ ...base, provider: 'libtv' })).toContain('历史任务')
  })

  it('identifies an interrupted Sol plan instead of blaming Kling or the image', () => {
    const message = providerActionRequiredMessage('AI_CREATIVE_INTERRUPTED', 'kling_official_api')
    expect(message).toContain('AI 编排任务')
    expect(message).toContain('后台服务中断')
    expect(message).not.toContain('检查素材和设置')
  })

  it('shows actionable provider failure details', () => {
    expect(variantExecutionMessage({
      generationStatus: 'FAILED',
      downloadStatus: 'FAILED',
      downloadArtifactId: null,
      errorCode: 'KLING_API_RATE_LIMITED',
      providerErrorMessage: 'Too many requests',
      providerErrorRetryable: true,
      provider: 'kling_official_api',
    })).toBe('生成服务现在比较繁忙，请稍等一会儿再试。 · 可以重试')
  })

  it('explains that unsupported Bach product references were never submitted', () => {
    const message = providerActionRequiredMessage('BACH_ELEMENTS_NOT_IN_P1', 'bach')
    expect(message).toContain('不能单独添加产品参考图')
    expect(message).toContain('本次尚未提交生成')
    expect(variantExecutionMessage({
      generationStatus: 'PENDING',
      downloadStatus: 'PENDING',
      downloadArtifactId: null,
      errorCode: null,
      provider: 'bach',
      providerTaskId: null,
    }, 'bach', 'ACTION_REQUIRED')).toBe(
      'Bach：任务暂时无法继续，请查看问题说明，或联系管理员。',
    )
  })
})
