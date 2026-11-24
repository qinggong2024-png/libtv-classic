import type { ImageGenerationClientManifest } from './types'
import { SHORT_MOHAIR_IMAGE_PROMPT } from './promptMaterialPresets'

export type ImageCenterTab = 'create' | 'results'
export type ImageProvider = 'gpt' | 'nanobanana'

export const IMAGE_PROVIDER_MODELS = {
  gpt: { label: 'GPT Image 2.5 Flare', modelId: 'openai/gpt-image-2.5-flare/edit' },
  nanobanana: { label: 'Nano Banana 2.1', modelId: 'google/nano-banana-2.1/edit' },
} as const

export function imageModelLabel(provider: ImageProvider, modelId?: string): string {
  if (modelId?.startsWith('openai/gpt-image-2/')) return 'GPT Image 2'
  if (modelId?.startsWith('google/nano-banana-2/')) return 'Nano Banana 2'
  return IMAGE_PROVIDER_MODELS[provider].label
}
export type ImageGenerationMode = 'fusion' | 'storyboard'
export type ImageTaskStatus = 'uploading' | 'submitting' | 'queued' | 'running' | 'completed' | 'failed' | 'write_uncertain' | 'manual_review'
export type ImageOutputKind = 'fusion' | 'storyboard'
export type ImageOutputView = 'front' | 'back' | 'shot'
export type ImageGenerationQuality = 'low' | 'medium' | 'high'

export interface ImageAssetSet {
  front: File | null
  back: File | null
  side: File | null
}

export interface OutfitDraft extends ImageAssetSet {
  id: string
  name: string
  sku: string
  promptOverride: string
}

export interface ImageCenterConfig {
  provider: ImageProvider
  modes: ImageGenerationMode[]
  resultCount: 1 | 2 | 3 | 4
  creativeBrief?: string
  fusionPrompt: string
  storyboardPrompt: string
  allowBackInference: boolean
  aspectRatio: string
  resolution: string
  quality: ImageGenerationQuality
  outputFormat: 'png'
}

export interface ImageGenerationOutput {
  id: string
  kind: ImageOutputKind
  view: ImageOutputView
  label: string
  file: File | null
  url: string
  downloadUrl?: string | null
  artifactId?: string | null
  qualificationStatus?: 'PENDING' | 'QUALIFIED' | 'REJECTED'
  qualityGates?: Record<string, { verification?: string; outcome?: string }>
  reviewedAt?: string | null
  humanReviewSeconds?: number | null
}

export interface ImageGenerationTask {
  id: string
  siteProfileId?: string
  clientTaskId?: string
  outfitId: string
  outfitName: string
  sku: string
  provider: ImageProvider
  modes: ImageGenerationMode[]
  resultCount: 1 | 2 | 3 | 4
  aspectRatio: string
  resolution: string
  quality: ImageGenerationQuality
  outputFormat: 'png'
  allowBackInference: boolean
  storyboardShots: StoryboardShot[]
  status: ImageTaskStatus
  batchId: string | null
  clientBatchId?: string | null
  batchName?: string
  providerTaskId: string | null
  providerTaskIds: string[]
  modelId: string
  createdAt: string
  prompt: {
    fusion: string
    storyboard: string
  }
  userRequirement?: string
  model: ImageAssetSet
  product: ImageAssetSet
  referenceUrls?: { model: string[]; product: string[] }
  outputs: ImageGenerationOutput[]
  error: string
  errorCode: string | null
  retryable: boolean | null
  errorDetails?: unknown
}

export function getOrderedSelectedTasks(tasks: ImageGenerationTask[], selectedOutputIds: string[]) {
  const selectedOrder = new Map(selectedOutputIds.map((id, index) => [id, index]))
  return tasks.map((task) => ({
    task,
    outputs: task.outputs.filter((output) => selectedOrder.has(output.id)).sort((left, right) => selectedOrder.get(left.id)! - selectedOrder.get(right.id)!),
  })).filter((item) => item.outputs.length)
}

export interface ImageCenterDraft {
  version: 1
  id: string
  name: string
  activeTab: ImageCenterTab
  siteProfileId?: string
  pendingDrafts?: ImageCenterDraftSnapshot[]
  draftOrder?: string[]
  pendingSubmission?: { draftId: string; clientBatchId: string } | null
  model: ImageAssetSet
  outfits: OutfitDraft[]
  config: ImageCenterConfig
  tasks: ImageGenerationTask[]
  selectedOutputIds: string[]
  updatedAt: string
}

export type ImageCenterDraftSnapshot = Pick<ImageCenterDraft, 'id' | 'name' | 'siteProfileId' | 'model' | 'outfits' | 'config'>

export interface StoryboardShot {
  id: string
  order: number
  instruction: string
}

export interface ImageTaskSummary {
  outfitCount: number
  fusionImages: number
  storyboardImages: number
  totalImages: number
  storyboardShotCount: number
  inferredBackOutfitCount: number
}

export const MAX_IMAGE_CENTER_OUTFITS = 20
export const MAX_IMAGE_GENERATIONS_PER_BATCH = 40
export function createEmptyImageAssetSet(): ImageAssetSet {
  return { front: null, back: null, side: null }
}

export function getImageAssetSlots(assets: ImageAssetSet): [File | null, File | null, File | null] {
  return [assets.front, assets.back, assets.side]
}

export function imageAssetSetFromSlots(slots: readonly (File | null)[]): ImageAssetSet {
  return {
    front: slots[0] ?? null,
    back: slots[1] ?? null,
    side: slots[2] ?? null,
  }
}

export function compactImageAssetSet(assets: ImageAssetSet): ImageAssetSet {
  return imageAssetSetFromSlots(getImageAssetSlots(assets).filter((file): file is File => file !== null))
}

export function swapImageAssetSlots(assets: ImageAssetSet, from: number, to: number): ImageAssetSet {
  if (from === to || from < 0 || from > 2 || to < 0 || to > 2) return assets
  const slots = getImageAssetSlots(assets)
  ;[slots[from], slots[to]] = [slots[to], slots[from]]
  return imageAssetSetFromSlots(slots)
}

export function moveImageAssetSlot(assets: ImageAssetSet, from: number, to: number): ImageAssetSet {
  const files = getImageAssetSlots(assets).filter((file): file is File => file !== null)
  if (from === to || from < 0 || from >= files.length || to < 0 || to > files.length) return assets
  const next = [...files]
  const [moved] = next.splice(from, 1)
  next.splice(Math.min(to, next.length), 0, moved)
  return imageAssetSetFromSlots(next)
}

export function createOutfit(id: string, index: number): OutfitDraft {
  return {
    id,
    name: `套装 ${String(index).padStart(2, '0')}`,
    sku: '',
    promptOverride: '',
    ...createEmptyImageAssetSet(),
  }
}

export function normalizeImageGenerationModes(
  modes: readonly ImageGenerationMode[] | null | undefined,
): [ImageGenerationMode] {
  const mode = modes?.find((item) => item === 'fusion' || item === 'storyboard')
  return [mode ?? 'fusion']
}

export function normalizeImageGenerationQuality(quality: unknown): ImageGenerationQuality {
  return quality === 'low' || quality === 'high' ? quality : 'medium'
}

export function createImageCenterRegenerationDraft(
  current: ImageCenterDraft,
  source: ImageGenerationTask,
  outfitId: string,
): ImageCenterDraft {
  return {
    ...current,
    activeTab: 'create',
    model: { ...source.model },
    outfits: [{
      id: outfitId,
      name: source.outfitName,
      sku: source.sku,
      promptOverride: '',
      ...source.product,
    }],
    config: {
      ...current.config,
      provider: source.provider,
      modes: normalizeImageGenerationModes(source.modes),
      resultCount: source.resultCount,
      creativeBrief: source.userRequirement ?? '',
      fusionPrompt: source.prompt.fusion,
      storyboardPrompt: source.prompt.storyboard,
      allowBackInference: source.allowBackInference,
      aspectRatio: source.aspectRatio,
      resolution: source.resolution,
      quality: source.quality,
      outputFormat: source.outputFormat,
    },
  }
}

export const DEFAULT_IMAGE_FUSION_PROMPT = '以图一为底图，仅将图一人物身上的上衣替换为图二的服装，完整保留图一原始背景、人物身份、脸部、发型、姿态、下装、构图、机位、光线和色调。不要抠出人物，不要移除、替换、虚化或重构背景。服装材质按图二准确还原：网眼球衣的小孔清晰稳定；短马海毛毛衣有细密短毛绒，领口和下摆都是短马海毛面料，不是罗纹。袖口同为短马海毛面料，不是罗纹，宽松版型，光线统一自然。衣服自然贴合身体，有自然褶皱和垂落，融合后衣服的色调光影和环境统一协调'
export const DEFAULT_IMAGE_STORYBOARD_PROMPT = '根据图一的人物和环境，生成一张人物别的姿势和拍摄角度的图片，保留人物脸部的毛孔和绒毛细节，自然的皮肤纹理，人物比例正常，保持图一的色调和光影不变，保持衣服的外观、印花和结构与已上传的服装参考图片一致。服装材质按商品参考图还原：短马海毛毛衣的领口和下摆都是短马海毛面料，不是罗纹。袖口同为短马海毛面料，不是罗纹，宽松版型，光线统一自然；网眼球衣的小孔清晰稳定。人物动作自然，表情放松，人物和环境比例大小正常，人物高度和透视与环境的关系参考图一的身高'

const LEGACY_DEFAULT_IMAGE_STORYBOARD_PROMPT = '根据图一的人物和环境，生成一张人物别的姿势和拍摄角度的图片，保留人物脸部的毛孔和绒毛细节，自然的皮肤纹理，人物比例正常，保持图一的色调和光影不变，保持衣服的印花和结构不变正面与图二一致，背面与图三一致，人物动作自然，表情放松，人物和环境比例大小正常，人物高度和透视与环境的关系参考图一的身高'

export function restoreImageDefaultPrompt(value: string, mode: ImageGenerationMode): string {
  const simplified = mode === 'fusion'
    ? '将产品服装自然穿在参考模特身上，保留人物身份、服装版型、颜色与纹理，呈现真实的服装展示照片。'
    : '生成一张服装视频分镜图，完整展示参考模特穿着产品服装，保持人物、服装细节和背景一致，构图清晰自然。'
  return !value.trim() || value.trim() === simplified || (mode === 'storyboard' && value.trim() === LEGACY_DEFAULT_IMAGE_STORYBOARD_PROMPT)
    ? mode === 'fusion' ? DEFAULT_IMAGE_FUSION_PROMPT : DEFAULT_IMAGE_STORYBOARD_PROMPT
    : value
}

export function createImageCenterDraft(id: string, outfitId: string, now = new Date().toISOString()): ImageCenterDraft {
  return {
    version: 1,
    id,
    name: '未命名图片批次',
    activeTab: 'create',
    model: createEmptyImageAssetSet(),
    outfits: [createOutfit(outfitId, 1)],
    config: {
      provider: 'nanobanana',
      modes: ['fusion'],
      resultCount: 1,
      creativeBrief: '',
      fusionPrompt: DEFAULT_IMAGE_FUSION_PROMPT,
      storyboardPrompt: DEFAULT_IMAGE_STORYBOARD_PROMPT,
      allowBackInference: false,
      aspectRatio: '9:16',
      resolution: '2k',
      quality: 'medium',
      outputFormat: 'png',
    },
    tasks: [],
    selectedOutputIds: [],
    updatedAt: now,
  }
}

export function parseStoryboardPrompt(prompt: string): StoryboardShot[] {
  const normalized = prompt.trim().replace(/\s+/g, ' ')
  if (!normalized) return []

  if (normalized.includes('生成一张')) {
    return [{ id: 'shot-1', order: 1, instruction: normalized }]
  }

  let parts = normalized
    .split(/[。！？!?；;\n]+/)
    .map((part) => part.trim())
    .filter(Boolean)

  if (parts.length === 1) {
    parts = normalized
      .split(/(?:，|,|然后|随后|最后|再拍|接着)/)
      .map((part) => part.trim().replace(/[。！？!?；;]+$/g, ''))
      .filter((part) => part.length >= 2)
  }

  return parts.slice(0, 7).map((instruction, index) => ({
    id: `shot-${index + 1}`,
    order: index + 1,
    instruction,
  }))
}

export function calculateImageTaskSummary(draft: ImageCenterDraft): ImageTaskSummary {
  const readyOutfits = draft.outfits.filter((outfit) => outfit.front)
  const storyboardShotCount = draft.config.modes.includes('storyboard')
    ? parseStoryboardPrompt(draft.config.storyboardPrompt).length
    : 0
  let fusionImages = 0
  const inferredBackOutfitCount = 0

  if (draft.config.modes.includes('fusion')) {
    fusionImages = readyOutfits.length * draft.config.resultCount
  }

  const storyboardImages = draft.config.modes.includes('storyboard')
    ? readyOutfits.length * storyboardShotCount * draft.config.resultCount
    : 0

  return {
    outfitCount: readyOutfits.length,
    fusionImages,
    storyboardImages,
    totalImages: fusionImages + storyboardImages,
    storyboardShotCount,
    inferredBackOutfitCount,
  }
}

export function validateImageCenterDraft(draft: ImageCenterDraft): string[] {
  const errors: string[] = []
  if (!draft.model.front) errors.push('请上传模特参考图。')
  if (!draft.outfits.some((outfit) => outfit.front)) errors.push('请至少上传一套服装参考图。')
  if (draft.config.modes.length !== 1) errors.push('请选择且只能选择一种生成任务。')
  if (draft.config.modes.includes('fusion') && !draft.config.fusionPrompt.trim()) errors.push('请填写多图融合提示词。')
  if (draft.config.modes.includes('storyboard') && !draft.config.storyboardPrompt.trim()) errors.push('请填写分镜图提示词。')
  if (draft.config.modes.includes('storyboard') && draft.config.storyboardPrompt.trim() && !parseStoryboardPrompt(draft.config.storyboardPrompt).length) {
    errors.push('分镜提示词暂时无法拆分，请补充镜头描述。')
  }
  if (draft.outfits.length > MAX_IMAGE_CENTER_OUTFITS) errors.push(`每个批次最多添加 ${MAX_IMAGE_CENTER_OUTFITS} 套产品。`)
  if (calculateImageTaskSummary(draft).totalImages > MAX_IMAGE_GENERATIONS_PER_BATCH) errors.push(`每个批次最多生成 ${MAX_IMAGE_GENERATIONS_PER_BATCH} 张图片，请减少套装、镜头或结果数量。`)
  return errors
}

export function buildWaitingTasks(
  draft: ImageCenterDraft,
  makeId: () => string,
  now = new Date().toISOString(),
): ImageGenerationTask[] {
  const userRequirement = (override: string) => [draft.config.creativeBrief?.trim(), override.trim()].filter(Boolean).join('\n')
  return draft.outfits.filter((outfit) => outfit.front).map((outfit) => {
    const outfitText = `${outfit.name} ${outfit.promptOverride}`
    const hasShortMohairOrSweater = /短马海毛|马海毛|mohair|毛衣|针织衫|sweater/i.test(outfitText)
    const hasOtherGarment = /球衣|T恤|卫衣|外套|衬衫|夹克|tee/i.test(outfitText)
    const shortMohair = hasShortMohairOrSweater && !hasOtherGarment
    const fusionPrompt = shortMohair && draft.config.fusionPrompt === DEFAULT_IMAGE_FUSION_PROMPT
      ? SHORT_MOHAIR_IMAGE_PROMPT
      : draft.config.fusionPrompt.trim()
    return {
    id: makeId(),
    outfitId: outfit.id,
    outfitName: outfit.name.trim() || '未命名套装',
    sku: outfit.sku.trim(),
    provider: draft.config.provider,
    modes: [...draft.config.modes],
    resultCount: draft.config.resultCount,
    aspectRatio: draft.config.aspectRatio,
    resolution: draft.config.resolution,
    quality: draft.config.quality,
    outputFormat: draft.config.outputFormat,
    allowBackInference: draft.config.allowBackInference,
    storyboardShots: parseStoryboardPrompt(draft.config.storyboardPrompt),
    status: 'uploading',
    batchId: null,
    clientBatchId: null,
    providerTaskId: null,
    providerTaskIds: [],
    modelId: IMAGE_PROVIDER_MODELS[draft.config.provider].modelId,
    createdAt: now,
    prompt: {
      fusion: fusionPrompt,
      storyboard: draft.config.storyboardPrompt.trim(),
    },
    userRequirement: userRequirement(outfit.promptOverride),
    model: { ...draft.model },
    product: { front: outfit.front, back: outfit.back, side: outfit.side },
    outputs: [],
    error: '',
    errorCode: null,
    retryable: null,
    }
  })
}

export type ImageReferenceRole = 'model.reference' | 'product.reference'

export function getOrderedImageReferences(
  model: ImageAssetSet,
  product: ImageAssetSet,
): Array<{ role: ImageReferenceRole; file: File }> {
  return [
    ...getImageAssetSlots(model).filter((file): file is File => file !== null).map((file) => ({ role: 'model.reference' as const, file })),
    ...getImageAssetSlots(product).filter((file): file is File => file !== null).map((file) => ({ role: 'product.reference' as const, file })),
  ]
}

export function buildImageGenerationBatchRequest(options: {
  clientBatchId: string
  name: string
  tasks: ImageGenerationTask[]
  expectedGenerationCount: number
}): { manifest: ImageGenerationClientManifest; files: File[] } {
  const first = options.tasks[0]
  if (!first) throw new Error('at least one image generation task is required')
  const files: File[] = []
  const fileIndexes = new Map<File, number>()
  const items = options.tasks.map((task) => {
    const orderedReferences = getOrderedImageReferences(task.model, task.product)
    const referenceRoles = orderedReferences.map(({ role, file }) => {
      let fileIndex = fileIndexes.get(file)
      if (fileIndex === undefined) {
        fileIndex = files.length
        files.push(file)
        fileIndexes.set(file, fileIndex)
      }
      return { fileIndex, role }
    })
    return {
      clientTaskId: task.id,
      outfitId: task.outfitId,
      outfitName: task.outfitName,
      sku: task.sku,
      prompts: task.prompt,
      userRequirement: task.userRequirement?.trim() || '',
      storyboardShots: task.storyboardShots.map(({ order, instruction }) => ({ order, instruction })),
      referenceFileIndexes: referenceRoles.map(({ fileIndex }) => fileIndex),
      referenceRoles,
      modelReferenceFileIndexes: orderedReferences
        .map(({ role, file }) => role === 'model.reference' ? fileIndexes.get(file) : undefined)
        .filter((index): index is number => index !== undefined),
      productReferenceFileIndexes: orderedReferences
        .map(({ role, file }) => role === 'product.reference' ? fileIndexes.get(file) : undefined)
        .filter((index): index is number => index !== undefined),
    }
  })
  return {
    files,
    manifest: {
      clientBatchId: options.clientBatchId,
      ...(first.siteProfileId ? { siteProfileId: first.siteProfileId } : {}),
      name: options.name,
      provider: first.provider,
      modes: first.modes,
      resultCount: first.resultCount,
      aspectRatio: first.aspectRatio,
      resolution: first.resolution,
      ...(first.provider === 'gpt' ? { quality: first.quality } : {}),
      outputFormat: 'png',
    allowBackInference: false,
      paidSubmissionConsent: true,
      expectedGenerationCount: options.expectedGenerationCount,
      items,
    },
  }
}
