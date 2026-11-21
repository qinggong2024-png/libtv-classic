export type VideoGenerationMode = 'automatic' | 'custom'
export type VideoResultCount = 1 | 2 | 3 | 4
export type VideoActionIntensity = 'low' | 'standard' | 'high'
export type VideoCutMode = 'explicit_cuts' | 'continuous_when_possible'
export type DirectorMode = 'cross_scene_v2' | 'reference_guided'

export interface VideoComposerSettings {
  templateId: string
  siteProfileId: string
  generationMode: VideoGenerationMode
  customPrompt: string
  resultCount: VideoResultCount
  durationSeconds: number
  actionIntensity: VideoActionIntensity
  cutMode: VideoCutMode
  directorMode: DirectorMode
}

export interface InitialVideoComposerPromptState {
  generationMode: VideoGenerationMode
  creativeBrief: string
  customPrompt: string
}

type ComposerStorage = Pick<Storage, 'getItem' | 'setItem'>

const KEYS = {
  templateId: 'fixed-video-template',
  siteProfileId: 'fixed-site-profile',
  resultCount: 'fixed-result-count',
  durationSeconds: 'fixed-duration-seconds',
  actionIntensity: 'fixed-action-intensity',
  cutMode: 'fixed-cut-mode',
  directorMode: 'fixed-director-mode',
} as const

export function loadVideoComposerSettings(storage: Pick<ComposerStorage, 'getItem'>): VideoComposerSettings {
  const resultCount = Number(storage.getItem(KEYS.resultCount))
  const durationSeconds = Number(storage.getItem(KEYS.durationSeconds))
  const actionIntensity = storage.getItem(KEYS.actionIntensity)
  const cutMode = storage.getItem(KEYS.cutMode)
  const directorMode = storage.getItem(KEYS.directorMode)

  return {
    templateId: storage.getItem(KEYS.templateId) ?? '',
    siteProfileId: storage.getItem(KEYS.siteProfileId) ?? '',
    // Prompt routes and drafts are task-scoped. A manual upload must never
    // inherit a previous task's direct-prompt route after a refresh.
    generationMode: 'automatic',
    customPrompt: '',
    resultCount: resultCount === 2 || resultCount === 3 || resultCount === 4 ? resultCount : 1,
    durationSeconds: Number.isInteger(durationSeconds) && durationSeconds >= 1 && durationSeconds <= 15 ? durationSeconds : 10,
    actionIntensity: actionIntensity === 'low' || actionIntensity === 'high' ? actionIntensity : 'standard',
    cutMode: cutMode === 'continuous_when_possible' ? 'continuous_when_possible' : 'explicit_cuts',
    directorMode: directorMode === 'reference_guided' ? 'reference_guided' : 'cross_scene_v2',
  }
}

export function saveVideoComposerSettings(storage: Pick<ComposerStorage, 'setItem'>, settings: VideoComposerSettings): void {
  storage.setItem(KEYS.templateId, settings.templateId)
  storage.setItem(KEYS.siteProfileId, settings.siteProfileId)
  storage.setItem(KEYS.resultCount, String(settings.resultCount))
  storage.setItem(KEYS.durationSeconds, String(settings.durationSeconds))
  storage.setItem(KEYS.actionIntensity, settings.actionIntensity)
  storage.setItem(KEYS.cutMode, settings.cutMode)
  storage.setItem(KEYS.directorMode, settings.directorMode)
}

export function initialVideoComposerPromptState(
  seedRequirement: string | null | undefined,
  settings: Pick<VideoComposerSettings, 'generationMode' | 'customPrompt'>,
  isRetiredVideoCopy: (content: string) => boolean,
): InitialVideoComposerPromptState {
  const guidance = seedRequirement?.trim() ?? ''
  const retireSeedCopy = Boolean(guidance) && isRetiredVideoCopy(guidance)
  const usableGuidance = retireSeedCopy ? '' : guidance
  const retireStoredCopy = isRetiredVideoCopy(settings.customPrompt)
  return {
    generationMode: retireSeedCopy || usableGuidance || retireStoredCopy
      ? 'automatic'
      : settings.generationMode,
    creativeBrief: usableGuidance,
    customPrompt: retireStoredCopy ? '' : settings.customPrompt,
  }
}
