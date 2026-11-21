import { describe, expect, it } from 'vitest'
import { buildWaitingTasks, createImageCenterDraft } from './imageCenterModel'
import { mergeImageHistory } from './imageCenterHistory'
import type { ImageGenerationBatch } from './types'

const batch = (id = 'batch-1'): ImageGenerationBatch => ({
  id, clientBatchId: `client-${id}`, provider: 'gpt', status: 'completed', createdAt: '2026-10-15T10:00:00Z',
  configuration: { modes: ['fusion'], resultCount: 2, aspectRatio: '3:4', resolution: '1k', quality: 'high', allowBackInference: false },
  items: [{ id: `item-${id}`, clientTaskId: 'task-1', outfitId: 'outfit-1', outfitName: '球衣', sku: 'SKU',
    status: 'completed', providerTaskId: 'remote-1', providerTaskIds: ['remote-1'], modelId: 'image-model',
    prompts: { fusion: '保留商品', storyboard: '' }, outputs: [{ id: `output-${id}`, kind: 'fusion', view: 'front', label: '正面', url: '/output', qualificationStatus: 'QUALIFIED' }],
  }],
})

describe('account image history', () => {
  it('restores results, configuration and review state without a browser draft', () => {
    const remote = batch()
    remote.configuration!.siteProfileId = 'pagewings'
    remote.items[0].userRequirement = '图二是服装背面，保留印花'
    const [restored] = mergeImageHistory([], [remote])
    expect(restored.userRequirement).toBe(remote.items[0].userRequirement)
    expect(restored.siteProfileId).toBe('pagewings')
    expect(restored).toMatchObject({ batchId: 'batch-1', clientTaskId: 'task-1', outfitName: '球衣', provider: 'gpt', quality: 'high', aspectRatio: '3:4', resultCount: 2, prompt: { fusion: '保留商品' } })
    expect(restored.outputs[0]).toMatchObject({ qualificationStatus: 'QUALIFIED', file: null, url: '/output' })
    expect(restored.model.front).toBeNull()
  })

  it('preserves local reference files and task identity, refreshes outputs and deduplicates pages', () => {
    const draft = createImageCenterDraft('draft', 'outfit-1')
    draft.model.front = new File(['model'], 'model.png', { type: 'image/png' })
    draft.outfits[0].front = new File(['product'], 'product.png', { type: 'image/png' })
    const [local] = buildWaitingTasks(draft, () => 'task-1')
    local.clientBatchId = 'client-batch-1'
    const merged = mergeImageHistory([local], [batch(), batch()])
    expect(merged).toHaveLength(1)
    expect(merged[0].id).toBe(local.id)
    expect(merged[0].model.front).toBe(draft.model.front)
    expect(merged[0].product.front).toBe(draft.outfits[0].front)
    expect(merged[0].status).toBe('completed')
    expect(mergeImageHistory(merged, [batch()])).toHaveLength(1)
  })

  it('keeps different batches with the same client task ID separate and retains local-only tasks', () => {
    const draft = createImageCenterDraft('draft', 'outfit')
    draft.outfits[0].front = new File(['product'], 'product.png', { type: 'image/png' })
    const [local] = buildWaitingTasks(draft, () => 'task-1')
    const merged = mergeImageHistory([local], [batch(), batch('batch-2')])
    expect(merged).toHaveLength(3)
    expect(new Set(merged.map((task) => task.id)).size).toBe(3)
    const updated = batch('batch-2')
    updated.items[0].status = 'failed'
    const refreshed = mergeImageHistory(merged, [updated])
    expect(refreshed.find((task) => task.batchId === 'batch-1')?.status).toBe('completed')
    expect(refreshed.find((task) => task.batchId === 'batch-2')?.status).toBe('failed')
    expect(refreshed.find((task) => !task.batchId)).toBe(local)
  })
})
