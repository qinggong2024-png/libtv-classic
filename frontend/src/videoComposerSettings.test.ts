import { describe, expect, it } from 'vitest'

import {
  initialVideoComposerPromptState,
  loadVideoComposerSettings,
  saveVideoComposerSettings,
} from './videoComposerSettings'

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    values,
  }
}

describe('video composer settings', () => {
  it('restores output parameters without restoring a task-specific prompt route', () => {
    const storage = memoryStorage({
      'fixed-video-template': 'template-2',
      'fixed-site-profile': 'profile-3',
      'fixed-generation-mode': 'custom',
      'fixed-custom-prompt': '视频专用动作与镜头提示词',
      'fixed-result-count': '4',
      'fixed-duration-seconds': '12',
      'fixed-action-intensity': 'high',
      'fixed-cut-mode': 'continuous_when_possible',
      'fixed-director-mode': 'reference_guided',
    })

    expect(loadVideoComposerSettings(storage)).toEqual({
      templateId: 'template-2',
      siteProfileId: 'profile-3',
      generationMode: 'automatic',
      customPrompt: '',
      resultCount: 4,
      durationSeconds: 12,
      actionIntensity: 'high',
      cutMode: 'continuous_when_possible',
      directorMode: 'reference_guided',
    })
  })

  it('uses safe defaults for missing or invalid stored values', () => {
    const storage = memoryStorage({
      'fixed-generation-mode': 'image',
      'fixed-result-count': '8',
      'fixed-action-intensity': 'extreme',
      'fixed-cut-mode': 'random',
    })

    expect(loadVideoComposerSettings(storage)).toMatchObject({
      generationMode: 'automatic',
      customPrompt: '',
      resultCount: 1,
      durationSeconds: 10,
      actionIntensity: 'standard',
      cutMode: 'explicit_cuts',
      directorMode: 'cross_scene_v2',
    })
  })

  it('restores Bach durations below the other providers minimum', () => {
    const storage = memoryStorage({ 'fixed-duration-seconds': '1' })

    expect(loadVideoComposerSettings(storage).durationSeconds).toBe(1)
  })

  it('persists output defaults without reusing custom prompts in a later task', () => {
    const storage = memoryStorage()
    saveVideoComposerSettings(storage, {
      templateId: 'template-1',
      siteProfileId: 'profile-1',
      generationMode: 'automatic',
      customPrompt: '保留的自定义视频提示词',
      resultCount: 3,
      durationSeconds: 15,
      actionIntensity: 'low',
      cutMode: 'explicit_cuts',
      directorMode: 'cross_scene_v2',
    })

    expect(Object.fromEntries(storage.values)).toEqual({
      'fixed-video-template': 'template-1',
      'fixed-site-profile': 'profile-1',
      'fixed-result-count': '3',
      'fixed-duration-seconds': '15',
      'fixed-action-intensity': 'low',
      'fixed-cut-mode': 'explicit_cuts',
      'fixed-director-mode': 'cross_scene_v2',
    })
  })

  it('absorbs handed-off requirements through automatic action selection', () => {
    expect(initialVideoComposerPromptState(
      '人物根据当前图片自然展示，并清楚推进动作。',
      { generationMode: 'custom', customPrompt: '旧的直出提示词' },
      () => false,
    )).toEqual({
      generationMode: 'automatic',
      creativeBrief: '人物根据当前图片自然展示，并清楚推进动作。',
      customPrompt: '旧的直出提示词',
    })
  })

  it('retires the former copied video preset instead of reusing it', () => {
    expect(initialVideoComposerPromptState(
      '',
      { generationMode: 'custom', customPrompt: 'former copied prompt' },
      (content) => content === 'former copied prompt',
    )).toEqual({
      generationMode: 'automatic',
      creativeBrief: '',
      customPrompt: '',
    })
  })

  it('does not turn a retired copied seed requirement into a creative brief', () => {
    expect(initialVideoComposerPromptState(
      'former copied prompt',
      { generationMode: 'custom', customPrompt: '' },
      (content) => content === 'former copied prompt',
    )).toEqual({
      generationMode: 'automatic',
      creativeBrief: '',
      customPrompt: '',
    })
  })
})
