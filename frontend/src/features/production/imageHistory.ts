import { fetchImageAssetFile } from '../../api'
import type { ImageGenerationTask, ImageGenerationOutput } from '../../imageCenterModel'
import { isSupportedImageFile } from '../../imageFiles'
import { createUuid } from '../../uuid'
import type { Material } from './productionTypes'

export type ImageHistoryEntry = { output: ImageGenerationOutput; outfitName: string }

export function imageHistoryEntries(tasks: ImageGenerationTask[]): ImageHistoryEntry[] {
  const seen = new Set<string>()
  return [...tasks].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).flatMap((task) => task.outputs.flatMap((output) => {
    if (seen.has(output.id) || (!output.file && !output.downloadUrl && !output.url)) return []
    seen.add(output.id)
    return [{ output, outfitName: task.outfitName }]
  }))
}

export async function prepareHistoryImages(entries: ImageHistoryEntry[]): Promise<Material[]> {
  return Promise.all(entries.map(async ({ output, outfitName }) => {
    const file = output.file ?? await fetchImageAssetFile(output.downloadUrl || output.url, `${outfitName}-${output.label}`)
    if (!isSupportedImageFile(file) || file.size <= 0 || file.size > 10 * 1024 * 1024) throw new Error(`“${output.label}”需要是非空的 JPG、PNG、WebP 图片，且不超过 10 MB。`)
    return { id: createUuid(), file, sourceImageOutputId: output.id }
  }))
}
