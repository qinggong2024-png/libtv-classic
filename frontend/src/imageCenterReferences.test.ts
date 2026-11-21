import { describe, expect, it, vi } from 'vitest'
import { buildWaitingTasks, createImageCenterDraft } from './imageCenterModel'
import { mergeImageHistory } from './imageCenterHistory'
import { resolveImageCenterReferences } from './imageCenterReferences'
import { createImageCenterVideoSeed, getImageCenterVideoStoryboardLimit } from './imageCenterVideoSeed'
import type { ImageGenerationBatch } from './types'

function file(name: string) {
  return new File([name], name, { type: 'image/png' })
}

describe('image center persisted source restoration', () => {
  it('loads all missing original product/model references and fills video seed without prompt changes', async () => {
    const draft = createImageCenterDraft('draft', 'outfit')
    draft.outfits[0].front = file('temporary-product')
    const [local] = buildWaitingTasks(draft, () => 'task')
    local.product.front = null
    local.clientBatchId = 'client-batch'
    const batch: ImageGenerationBatch = {
      id: 'batch', clientBatchId: 'client-batch', status: 'completed', createdAt: '2026-11-02T00:00:00Z',
      items: [{
        id: 'item', clientTaskId: 'task', outfitId: 'outfit', status: 'completed',
        providerTaskId: null, providerTaskIds: [], modelId: 'model',
        referenceUrls: {
          model: ['/model-1', '/model-2'],
          product: ['/product-1', '/product-2', '/product-3'],
        },
        outputs: [],
      }],
    }
    const [historical] = mergeImageHistory([local], [batch])
    const fetch = vi.fn(async (url: string) => file(url))
    const restored = await resolveImageCenterReferences(historical, fetch)
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      '/model-1', '/model-2', '/product-1', '/product-2', '/product-3',
    ])
    expect(getImageCenterVideoStoryboardLimit(historical)).toBe(4)
    const storyboard = file('shot')
    const seed = createImageCenterVideoSeed(restored, [storyboard], ['shot-id'], 'seed')
    expect(seed.products.map((f) => f.name)).toEqual(['/product-1', '/product-2', '/product-3'])
    expect(seed.storyboards).toEqual([storyboard])
    expect(seed.subjectFiles.detailImages).toHaveLength(1)
    expect(seed).not.toHaveProperty('prompt')
  })

  it('keeps local original files without re-downloading them', async () => {
    const draft = createImageCenterDraft('draft', 'outfit')
    const model = file('original-model.png')
    const product = file('original-product.png')
    draft.model.front = model
    draft.outfits[0].front = product
    const [task] = buildWaitingTasks(draft, () => 'task')
    task.referenceUrls = { model: ['/m'], product: ['/p'] }
    const fetch = vi.fn(async (url: string) => file(url))
    const restored = await resolveImageCenterReferences(task, fetch)
    expect(restored.model.front).toBe(model)
    expect(restored.product.front).toBe(product)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('leaves old history unchanged when no original source is recoverable', async () => {
    const draft = createImageCenterDraft('draft', 'outfit')
    draft.outfits[0].front = file('temporary-product')
    const [task] = buildWaitingTasks(draft, () => 'task')
    task.product.front = null
    expect(await resolveImageCenterReferences(task)).toBe(task)
  })
})
