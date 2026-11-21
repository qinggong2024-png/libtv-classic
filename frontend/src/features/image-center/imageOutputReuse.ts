import { fetchImageAssetFile } from '../../api'
import { isSupportedImageFile } from '../../imageFiles'
import type { ImageGenerationOutput } from '../../imageCenterModel'

export function parseImageOutputDrag(payload: string): { taskId: string; outputId: string } | null {
  try {
    const value: unknown = JSON.parse(payload)
    if (!value || typeof value !== 'object') return null
    const { taskId, outputId } = value as Record<string, unknown>
    return typeof taskId === 'string' && taskId && typeof outputId === 'string' && outputId
      ? { taskId, outputId }
      : null
  } catch {
    return null
  }
}

export async function prepareImageOutputReference(output: ImageGenerationOutput): Promise<File> {
  const path = output.downloadUrl || output.url
  if (!output.file && !path) throw new Error('这张结果图暂无可用文件，请重新选择。')
  const file = output.file ?? await fetchImageAssetFile(path, output.label)
  if (!isSupportedImageFile(file) || file.size === 0) {
    throw new Error('结果图需要是非空的 JPG、PNG 或 WebP 图片，请下载检查后重试。')
  }
  return file
}
