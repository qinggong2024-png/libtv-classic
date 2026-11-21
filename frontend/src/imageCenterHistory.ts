import { createEmptyImageAssetSet, normalizeImageGenerationQuality } from './imageCenterModel'
import type { ImageGenerationTask } from './imageCenterModel'
import type { ImageGenerationBatch } from './types'

// Keep local reference files and selection identities while making server results authoritative.
export function mergeImageHistory(tasks: ImageGenerationTask[], batches: ImageGenerationBatch[]): ImageGenerationTask[] {
  const merged = [...tasks]
  for (const batch of batches) {
    for (const item of batch.items) {
      const index = merged.findIndex((task) => (task.clientTaskId ?? task.id) === item.clientTaskId
        && (task.batchId === batch.id || (!task.batchId && Boolean(batch.clientBatchId) && task.clientBatchId === batch.clientBatchId)))
      const local = index >= 0 ? merged[index] : undefined
      const config = batch.configuration
      const task: ImageGenerationTask = {
        id: local?.id ?? `history-${batch.id}-${item.id}`,
        clientTaskId: item.clientTaskId,
        siteProfileId: config?.siteProfileId ?? local?.siteProfileId,
        outfitId: item.outfitId,
        outfitName: item.outfitName ?? local?.outfitName ?? batch.name ?? '历史生图',
        sku: item.sku ?? local?.sku ?? '',
        provider: batch.provider ?? local?.provider ?? 'nanobanana',
        modes: config?.modes ?? local?.modes ?? ['fusion'],
        resultCount: config?.resultCount ?? local?.resultCount ?? 1,
        aspectRatio: config?.aspectRatio ?? local?.aspectRatio ?? '9:16',
        resolution: config?.resolution ?? local?.resolution ?? '2k',
        quality: normalizeImageGenerationQuality(config?.quality ?? local?.quality),
        outputFormat: 'png',
        allowBackInference: config?.allowBackInference ?? local?.allowBackInference ?? true,
        storyboardShots: item.storyboardShots?.map((shot) => ({ ...shot, id: `shot-${shot.order}` })) ?? local?.storyboardShots ?? [],
        status: item.status,
        batchId: batch.id,
        clientBatchId: batch.clientBatchId ?? local?.clientBatchId ?? null,
        providerTaskId: item.providerTaskId,
        providerTaskIds: item.providerTaskIds ?? (item.providerTaskId ? [item.providerTaskId] : []),
        modelId: item.modelId || local?.modelId || '',
        createdAt: batch.createdAt ?? local?.createdAt ?? '',
        prompt: item.prompts ?? local?.prompt ?? { fusion: '', storyboard: '' },
        userRequirement: item.userRequirement ?? local?.userRequirement ?? '',
        model: local?.model ?? createEmptyImageAssetSet(),
        product: local?.product ?? createEmptyImageAssetSet(),
        referenceUrls: item.referenceUrls ?? local?.referenceUrls,
        outputs: item.outputs.map((output) => ({ ...output, file: null, url: output.downloadUrl || output.url })),
        error: item.error?.message ?? '',
        errorCode: item.error?.code ?? null,
        retryable: item.error?.retryable ?? null,
        errorDetails: item.error?.details,
      }
      if (index >= 0) merged[index] = task
      else merged.push(task)
    }
  }
  return merged.sort((a, b) => b.createdAt.localeCompare(a.createdAt))
}
