import { describe, expect, it } from 'vitest'
import {
  buildWaitingTasks,
  buildImageGenerationBatchRequest,
  calculateImageTaskSummary,
  compactImageAssetSet,
  createImageCenterDraft,
  createImageCenterRegenerationDraft,
  getImageAssetSlots,
  getOrderedImageReferences,
  getOrderedSelectedTasks,
  imageAssetSetFromSlots,
  imageModelLabel,
  moveImageAssetSlot,
  normalizeImageGenerationModes,
  normalizeImageGenerationQuality,
  parseStoryboardPrompt,
  restoreImageDefaultPrompt,
  swapImageAssetSlots,
  validateImageCenterDraft,
} from './imageCenterModel'
import { SHORT_MOHAIR_IMAGE_PROMPT } from './promptMaterialPresets'

function image(name: string): File {
  return new File(['image'], name, { type: 'image/jpeg' })
}

describe('image center model', () => {
  it('restores simplified defaults without replacing custom prompts', () => {
    const defaults = createImageCenterDraft('batch', 'outfit').config
    expect(restoreImageDefaultPrompt('将产品服装自然穿在参考模特身上，保留人物身份、服装版型、颜色与纹理，呈现真实的服装展示照片。', 'fusion')).toBe(defaults.fusionPrompt)
    expect(restoreImageDefaultPrompt('生成一张服装视频分镜图，完整展示参考模特穿着产品服装，保持人物、服装细节和背景一致，构图清晰自然。', 'storyboard')).toBe(defaults.storyboardPrompt)
    expect(restoreImageDefaultPrompt('', 'fusion')).toBe(defaults.fusionPrompt)
    expect(restoreImageDefaultPrompt('我的自定义要求', 'storyboard')).toBe('我的自定义要求')
    expect(defaults.fusionPrompt).toContain('不要抠出人物')
    expect(defaults.fusionPrompt).toContain('领口和下摆都是短马海毛面料')
    expect(defaults.fusionPrompt).toContain('袖口同为短马海毛面料')
    expect(defaults.fusionPrompt).toContain('不是罗纹')
    expect(defaults.fusionPrompt).not.toContain('螺纹')
    expect(defaults.fusionPrompt).not.toContain('替换为图二的球衣')
    expect(defaults.storyboardPrompt).toContain('已上传的服装参考图片一致')
    expect(defaults.storyboardPrompt).toContain('领口和下摆都是短马海毛面料')
    expect(defaults.storyboardPrompt).toContain('袖口同为短马海毛面料')
    expect(defaults.storyboardPrompt).toContain('不是罗纹')
    expect(defaults.storyboardPrompt).not.toContain('螺纹')
    expect(defaults.storyboardPrompt).not.toContain('图三')
    expect(restoreImageDefaultPrompt('根据图一的人物和环境，生成一张人物别的姿势和拍摄角度的图片，保留人物脸部的毛孔和绒毛细节，自然的皮肤纹理，人物比例正常，保持图一的色调和光影不变，保持衣服的印花和结构不变正面与图二一致，背面与图三一致，人物动作自然，表情放松，人物和环境比例大小正常，人物高度和透视与环境的关系参考图一的身高', 'storyboard')).toBe(defaults.storyboardPrompt)
  })
  it('lets beginners generate with defaults and keeps optional prose from creating extra shots', () => {
    const draft = createImageCenterDraft('batch', 'outfit')
    draft.model.front = image('model.jpg')
    draft.outfits[0].front = image('product.jpg')
    expect(validateImageCenterDraft(draft)).toEqual([])
    draft.config.creativeBrief = '自然转身。展示细节。最后微笑。'
    const fusion = buildWaitingTasks(draft, () => 'fusion')[0]
    expect(fusion.prompt.fusion).toBe(draft.config.fusionPrompt)
    expect(fusion.userRequirement).toBe(draft.config.creativeBrief)
    const { manifest } = buildImageGenerationBatchRequest({ clientBatchId: 'optional-brief', name: draft.name, tasks: [fusion], expectedGenerationCount: 1 })
    expect(manifest.items[0].prompts.fusion).toBe(draft.config.fusionPrompt)
    expect(manifest.items[0].userRequirement).toBe(draft.config.creativeBrief)
    draft.config.modes = ['storyboard']
    expect(calculateImageTaskSummary(draft).storyboardShotCount).toBe(1)
    const storyboard = buildWaitingTasks(draft, () => 'storyboard')[0]
    expect(storyboard.storyboardShots).toHaveLength(1)
    expect(storyboard.storyboardShots[0].instruction).not.toContain(draft.config.creativeBrief)
    expect(storyboard.userRequirement).toBe(draft.config.creativeBrief)
    const regenerated = createImageCenterRegenerationDraft(draft, storyboard, 'again')
    expect(regenerated.config.creativeBrief).toBe(draft.config.creativeBrief)
    expect(buildWaitingTasks(regenerated, () => 'again')[0].userRequirement).toBe(draft.config.creativeBrief)
  })

  it('uses the dedicated short mohair image prompt when the outfit explicitly names it', () => {
    const draft = createImageCenterDraft('batch', 'outfit')
    draft.model.front = image('model.jpg')
    draft.outfits[0].name = '短马海毛毛衣'
    draft.outfits[0].front = image('product.jpg')

    const task = buildWaitingTasks(draft, () => 'fusion')[0]

    expect(task.prompt.fusion).toContain('图1模特穿上图2的短马海毛毛衣')
    expect(task.prompt.fusion).toContain('图2衣服图案版型100%还原')
  })

  it('does not apply the short mohair prompt to other garment types', () => {
    const draft = createImageCenterDraft('batch', 'outfit')
    draft.model.front = image('model.jpg')
    draft.outfits[0].name = '短马海毛T恤'
    draft.outfits[0].front = image('product.jpg')

    const task = buildWaitingTasks(draft, () => 'fusion')[0]

    expect(task.prompt.fusion).toBe(draft.config.fusionPrompt)
  })

  it('treats sweater and short mohair as the same material category', () => {
    const draft = createImageCenterDraft('batch', 'outfit')
    draft.model.front = image('model.jpg')
    draft.outfits[0].name = '毛衣'
    draft.outfits[0].front = image('product.jpg')

    const task = buildWaitingTasks(draft, () => 'fusion')[0]

    expect(task.prompt.fusion).toBe(SHORT_MOHAIR_IMAGE_PROMPT)
  })

  it('uses upgraded models for new and regenerated tasks while labeling legacy history accurately', () => {
    const draft = createImageCenterDraft('batch', 'outfit')
    draft.model.front = image('model.jpg')
    draft.outfits[0].front = image('product.jpg')
    for (const [provider, modelId, label] of [
      ['gpt', 'openai/gpt-image-2.5-flare/edit', 'GPT Image 2.5 Flare'],
      ['nanobanana', 'google/nano-banana-2.1/edit', 'Nano Banana 2.1'],
    ] as const) {
      draft.config.provider = provider
      const task = buildWaitingTasks(draft, () => 'task')[0]
      expect(task.modelId).toBe(modelId)
      expect(imageModelLabel(provider)).toBe(label)
      expect(imageModelLabel(provider, modelId)).toBe(label)
      const legacyModelId = provider === 'gpt' ? 'openai/gpt-image-2/edit' : 'google/nano-banana-2/edit'
      const legacyTask = { ...task, modelId: legacyModelId }
      const regenerated = createImageCenterRegenerationDraft(draft, legacyTask, 'next-outfit')
      expect(buildWaitingTasks(regenerated, () => 'next-task')[0].modelId).toBe(modelId)
      expect(legacyTask.modelId).toBe(legacyModelId)
    }
    expect(imageModelLabel('gpt', 'openai/gpt-image-2/edit')).toBe('GPT Image 2')
    expect(imageModelLabel('nanobanana', 'google/nano-banana-2/edit')).toBe('Nano Banana 2')
  })

  it('normalizes legacy generation modes to one mutually exclusive task', () => {
    expect(normalizeImageGenerationModes(['storyboard', 'fusion'])).toEqual(['storyboard'])
    expect(normalizeImageGenerationModes([])).toEqual(['fusion'])

    const draft = createImageCenterDraft('batch', 'outfit')
    draft.config.modes = ['fusion', 'storyboard']
    expect(validateImageCenterDraft(draft)).toContain('请选择且只能选择一种生成任务。')
  })

  it('restores only supported image quality values', () => {
    expect(normalizeImageGenerationQuality('low')).toBe('low')
    expect(normalizeImageGenerationQuality('high')).toBe('high')
    expect(normalizeImageGenerationQuality('legacy-ultra')).toBe('medium')
    expect(normalizeImageGenerationQuality(undefined)).toBe('medium')
  })

  it('treats back and side images as optional', () => {
    const draft = createImageCenterDraft('batch', 'outfit')
    draft.model.front = image('model-front.jpg')
    draft.outfits[0].front = image('product-front.jpg')
    draft.config.fusionPrompt = '保持服装准确'

    expect(validateImageCenterDraft(draft)).toEqual([])
  })

  it('parses one storyboard paragraph into ordered shots', () => {
    expect(parseStoryboardPrompt('模特走向镜头，展示服装正面，随后转身展示背面，最后拍摄面料细节。'))
      .toMatchObject([
        { order: 1, instruction: '模特走向镜头' },
        { order: 2, instruction: '展示服装正面' },
        { order: 3, instruction: '转身展示背面' },
        { order: 4, instruction: '拍摄面料细节' },
      ])
  })

  it('keeps a generate-one-image storyboard preset as one shot', () => {
    const prompt = '根据图一的人物和环境，生成一张人物别的姿势和拍摄角度的图片，正面与图二一致，背面与图三一致'

    expect(parseStoryboardPrompt(prompt)).toEqual([
      { id: 'shot-1', order: 1, instruction: prompt },
    ])
  })

  it('does not require a back image for storyboard prompts that refer to clothing references', () => {
    const draft = createImageCenterDraft('batch', 'outfit')
    draft.model.front = image('model-front.jpg')
    draft.outfits[0].front = image('product-front.jpg')
    draft.config.modes = ['storyboard']
    draft.config.storyboardPrompt = '根据图一生成一张图片，正面与图二一致，背面与图三一致'

    expect(validateImageCenterDraft(draft)).toEqual([])

    draft.outfits[0].back = image('product-back.jpg')
    expect(validateImageCenterDraft(draft)).toEqual([])
  })

  it('calculates fusion and storyboard output volume for all ready outfits', () => {
    const draft = createImageCenterDraft('batch', 'outfit-1')
    draft.model.front = image('model-front.jpg')
    draft.model.back = image('model-back.jpg')
    draft.outfits[0].front = image('product-1-front.jpg')
    draft.outfits[0].back = image('product-1-back.jpg')
    draft.outfits.push({
      id: 'outfit-2',
      name: '套装 02',
      sku: '',
      promptOverride: '',
      front: image('product-2-front.jpg'),
      back: null,
      side: null,
    })
    draft.config.modes = ['fusion', 'storyboard']
    draft.config.resultCount = 2
    draft.config.storyboardPrompt = '正面全身。背面展示。细节特写。'

    expect(calculateImageTaskSummary(draft)).toEqual({
      outfitCount: 2,
      fusionImages: 4,
      storyboardImages: 12,
      totalImages: 16,
      storyboardShotCount: 3,
      inferredBackOutfitCount: 0,
    })
  })

  it('creates one independent image task for each outfit', () => {
    const draft = createImageCenterDraft('batch', 'outfit-1')
    draft.model.front = image('model-front.jpg')
    draft.outfits[0].front = image('product-front.jpg')
    draft.config.fusionPrompt = '保持服装准确。'
    draft.config.quality = 'low'
    draft.outfits.push({
      id: 'outfit-2',
      name: '第二套',
      sku: 'SKU-2',
      promptOverride: '单独提示词',
      front: image('second-front.jpg'),
      back: null,
      side: null,
    })
    let nextId = 0
    const tasks = buildWaitingTasks(draft, () => `task-${++nextId}`, '2026-07-31T00:00:00.000Z')

    expect(tasks).toHaveLength(2)
    expect(tasks.map((task) => task.outfitId)).toEqual(['outfit-1', 'outfit-2'])
    expect(tasks[1].prompt.fusion).toBe(draft.config.fusionPrompt)
    expect(tasks[1].userRequirement).toBe('单独提示词')
    expect(tasks.every((task) => task.status === 'uploading')).toBe(true)
    expect(tasks[0]).toMatchObject({
      aspectRatio: '9:16',
      resolution: '2k',
      quality: 'low',
      outputFormat: 'png',
      modelId: 'google/nano-banana-2.1/edit',
    })
  })

  it('restores one result task as an editable draft without removing task history', () => {
    const draft = createImageCenterDraft('batch', 'outfit-1')
    const modelFront = image('model-front.jpg')
    const productFront = image('product-front.jpg')
    draft.model.front = modelFront
    draft.outfits[0] = {
      ...draft.outfits[0],
      name: '套装 01',
      sku: 'SKU-01',
      front: productFront,
      promptOverride: '保留印花细节',
    }
    draft.config.provider = 'gpt'
    draft.config.modes = ['storyboard']
    draft.config.resultCount = 3
    draft.config.aspectRatio = '3:4'
    draft.config.resolution = '4k'
    draft.config.quality = 'high'
    draft.config.allowBackInference = false
    draft.config.storyboardPrompt = '正面展示。转身展示。'
    const source = buildWaitingTasks(draft, () => 'task-1')[0]
    source.status = 'completed'
    source.outputs = [{ id: 'output-1', kind: 'storyboard', view: 'shot', label: '分镜 1', file: null, url: 'result.png' }]
    draft.tasks = [source]
    draft.selectedOutputIds = ['output-1']

    const restored = createImageCenterRegenerationDraft(draft, source, 'regenerated-outfit')

    expect(restored.activeTab).toBe('create')
    expect(restored.tasks).toBe(draft.tasks)
    expect(restored.selectedOutputIds).toBe(draft.selectedOutputIds)
    expect(restored.model.front).toBe(modelFront)
    expect(restored.outfits).toEqual([{
      id: 'regenerated-outfit',
      name: '套装 01',
      sku: 'SKU-01',
      promptOverride: '',
      front: productFront,
      back: null,
      side: null,
    }])
    expect(restored.config).toMatchObject({
      provider: 'gpt',
      modes: ['storyboard'],
      resultCount: 3,
      aspectRatio: '3:4',
      resolution: '4k',
      quality: 'high',
      allowBackInference: false,
      storyboardPrompt: '正面展示。转身展示。',
      creativeBrief: '保留印花细节',
    })
  })

  it('groups selected outputs by product while preserving the storyboard order', () => {
    const draft = createImageCenterDraft('batch', 'outfit-1')
    draft.model.front = image('model-front.jpg')
    draft.outfits[0].front = image('product-1-front.jpg')
    draft.outfits.push({ id: 'outfit-2', name: '第二套', sku: 'SKU-2', promptOverride: '', front: image('product-2-front.jpg'), back: null, side: null })
    let nextId = 0
    const tasks = buildWaitingTasks(draft, () => `task-${++nextId}`)
    tasks[0].outputs = [
      { id: 'frame-a', kind: 'storyboard', view: 'shot', label: '分镜 A', file: null, url: 'a.png' },
      { id: 'frame-b', kind: 'storyboard', view: 'shot', label: '分镜 B', file: null, url: 'b.png' },
    ]
    tasks[1].outputs = [{ id: 'frame-c', kind: 'storyboard', view: 'shot', label: '分镜 C', file: null, url: 'c.png' }]

    const groups = getOrderedSelectedTasks(tasks, ['frame-b', 'frame-c', 'frame-a'])

    expect(groups.map(({ task }) => task.outfitId)).toEqual(['outfit-1', 'outfit-2'])
    expect(groups[0].outputs.map((output) => output.id)).toEqual(['frame-b', 'frame-a'])
    expect(groups[1].outputs.map((output) => output.id)).toEqual(['frame-c'])
  })

  it('keeps front, back and side images in explicit sortable slots', () => {
    const front = image('front.jpg')
    const back = image('back.jpg')
    const side = image('side.jpg')
    const assets = imageAssetSetFromSlots([front, back, side])

    expect(getImageAssetSlots(assets)).toEqual([front, back, side])
    expect(getImageAssetSlots(swapImageAssetSlots(assets, 0, 2))).toEqual([side, back, front])
    expect(swapImageAssetSlots(assets, -1, 2)).toBe(assets)
  })

  it('moves images with the same insertion behavior as storyboard sorting', () => {
    const front = image('front.jpg')
    const back = image('back.jpg')
    const side = image('side.jpg')
    const assets = imageAssetSetFromSlots([front, back, side])

    expect(getImageAssetSlots(moveImageAssetSlot(assets, 0, 2))).toEqual([back, side, front])
    expect(getImageAssetSlots(moveImageAssetSlot(assets, 2, 1))).toEqual([front, side, back])
    expect(moveImageAssetSlot(assets, 3, 0)).toBe(assets)
  })

  it('compacts legacy sparse slots into the new ordered upload list', () => {
    const front = image('front.jpg')
    const side = image('side.jpg')

    expect(getImageAssetSlots(compactImageAssetSet({ front, back: null, side }))).toEqual([front, side, null])
  })

  it('orders provider references explicitly across model and product images', () => {
    const model = {
      front: image('model-front.jpg'),
      back: image('model-back.jpg'),
      side: image('model-side.jpg'),
    }
    const product = {
      front: image('product-front.jpg'),
      back: image('product-back.jpg'),
      side: image('product-side.jpg'),
    }

    expect(getOrderedImageReferences(model, product).map(({ role, file }) => [role, file.name])).toEqual([
      ['model.reference', 'model-front.jpg'],
      ['model.reference', 'model-back.jpg'],
      ['model.reference', 'model-side.jpg'],
      ['product.reference', 'product-front.jpg'],
      ['product.reference', 'product-back.jpg'],
      ['product.reference', 'product-side.jpg'],
    ])
  })

  it('uploads shared model files once while preserving per-outfit reference indexes', () => {
    const draft = createImageCenterDraft('batch', 'outfit-1')
    draft.model.front = image('model-front.jpg')
    draft.outfits[0].front = image('product-1.jpg')
    draft.outfits.push({
      id: 'outfit-2',
      name: '套装 02',
      sku: '',
      promptOverride: '',
      front: image('product-2.jpg'),
      back: null,
      side: null,
    })
    let nextId = 0
    const tasks = buildWaitingTasks(draft, () => `task-${++nextId}`)

    const { manifest, files } = buildImageGenerationBatchRequest({
      clientBatchId: 'client-batch',
      name: draft.name,
      tasks,
      expectedGenerationCount: 2,
    })

    expect(files.map((file) => file.name)).toEqual(['model-front.jpg', 'product-1.jpg', 'product-2.jpg'])
    expect(manifest.items[0].referenceFileIndexes).toEqual([0, 1])
    expect(manifest.items[1].referenceFileIndexes).toEqual([0, 2])
    expect(manifest.items[1].referenceRoles).toEqual([
      { fileIndex: 0, role: 'model.reference' },
      { fileIndex: 2, role: 'product.reference' },
    ])
    expect(manifest.items[0].prompts.fusion).toBe(draft.config.fusionPrompt)
    expect(manifest.items[0].userRequirement).toBe('')
  })

  it('blocks batches whose server-expanded output count exceeds the 40 image limit', () => {
    const draft = createImageCenterDraft('batch', 'outfit-1')
    draft.model.front = image('model-front.jpg')
    draft.model.back = image('model-back.jpg')
    draft.outfits[0].front = image('product-1-front.jpg')
    draft.outfits[0].back = image('product-1-back.jpg')
    draft.outfits.push({
      id: 'outfit-2',
      name: '套装 02',
      sku: '',
      promptOverride: '',
      front: image('product-2-front.jpg'),
      back: image('product-2-back.jpg'),
      side: null,
    })
    draft.config.modes = ['fusion', 'storyboard']
    draft.config.resultCount = 4
    draft.config.storyboardPrompt = '镜头一。镜头二。镜头三。镜头四。'

    expect(calculateImageTaskSummary(draft).totalImages).toBe(40)
    expect(validateImageCenterDraft(draft)).not.toContain('每个批次最多生成 40 张图片，请减少套装、镜头或结果数量。')
  })

  it('accepts all six classic references for Nano Banana 2.1', () => {
    const draft = createImageCenterDraft('batch', 'outfit-1')
    draft.config.fusionPrompt = '保持服装准确。'
    draft.model = {
      front: image('model-front.jpg'),
      back: image('model-back.jpg'),
      side: image('model-side.jpg'),
    }
    Object.assign(draft.outfits[0], {
      front: image('product-front.jpg'),
      back: image('product-back.jpg'),
      side: image('product-side.jpg'),
    })

    expect(getOrderedImageReferences(draft.model, draft.outfits[0])).toHaveLength(6)
    expect(validateImageCenterDraft(draft)).toEqual([])
  })
})
