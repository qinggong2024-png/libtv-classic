import { expect, it } from 'vitest'
import { buildImageGenerationBatchRequest, buildWaitingTasks, createImageCenterDraft } from './imageCenterModel'

it('submits brand metadata separately from the unchanged image prompts', () => {
  const draft = createImageCenterDraft('draft', 'outfit')
  draft.model.front = new File(['model'], 'model.png', { type: 'image/png' })
  draft.outfits[0].front = new File(['product'], 'product.png', { type: 'image/png' })
  const tasks = buildWaitingTasks(draft, () => 'task').map((task) => ({ ...task, siteProfileId: 'pagewings' }))
  const { manifest } = buildImageGenerationBatchRequest({ clientBatchId: 'batch', name: draft.name, tasks, expectedGenerationCount: 1 })
  expect(manifest.siteProfileId).toBe('pagewings')
  expect(manifest.items[0].prompts).toEqual(tasks[0].prompt)
  expect(manifest.items[0].prompts.fusion).toBe(draft.config.fusionPrompt)
})
