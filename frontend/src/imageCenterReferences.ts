import { fetchImageAssetFile } from './api'
import { getImageAssetSlots, imageAssetSetFromSlots } from './imageCenterModel'
import type { ImageAssetSet, ImageGenerationTask } from './imageCenterModel'

export async function resolveImageCenterReferences(
  task: ImageGenerationTask,
  fetchReference: (url: string, filename: string) => Promise<File> = fetchImageAssetFile,
): Promise<ImageGenerationTask> {
  async function restoreGroup(group: ImageAssetSet, urls: string[], label: string): Promise<ImageAssetSet> {
    const files = await Promise.all(getImageAssetSlots(group).map(async (file, index) =>
      file ?? (urls[index] ? fetchReference(urls[index], `${task.outfitName}-${label}-${index + 1}`) : null)
    ))
    return imageAssetSetFromSlots(files)
  }
  const references = task.referenceUrls
  if (!references?.model.length && !references?.product.length) return task
  const [model, product] = await Promise.all([
    restoreGroup(task.model, references.model, '模特'),
    restoreGroup(task.product, references.product, '商品'),
  ])
  return { ...task, model, product }
}
