import type { DeepVideoMode } from './api'

export type ReferenceVideoMetadata = { duration: number; width: number; height: number }

export function referenceVideoError(mode: DeepVideoMode, metadata: ReferenceVideoMetadata | null): string {
  if (!metadata) return '正在读取视频时长与尺寸，请稍候。'
  const { duration, width, height } = metadata
  if (!Number.isFinite(duration) || duration <= 0 || width <= 0 || height <= 0) return '无法读取视频信息，请更换有效的 MP4 或 MOV 视频。'
  if (duration > 600) return '参考视频最长支持 10 分钟，请先裁剪。'
  if (mode === 'recreate' && (duration < 4 || duration > 15)) return '人物与商品替换需要 4–15 秒视频，请先裁剪或切换为仅提取深度视频。'
  return ''
}
