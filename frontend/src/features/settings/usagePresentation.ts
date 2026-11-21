import type { AccountUsageReport } from '../../types'

type UsageItem = Pick<AccountUsageReport['items'][number], 'kind' | 'category' | 'units'>
const categories: Record<string, string> = { IMAGE: '图片生成', VIDEO: '视频生成', DEPTH_VIDEO: '提取深度视频', RECREATE_VIDEO: '人物／产品替换', RECREATE_DEPTH_VIDEO: '深度提取＋人物／产品替换' }

export function usagePresentation(item: UsageItem) {
  if (item.kind === 'CUTFLOW' || item.category === 'CUTFLOW') {
    return { label: '视频剪辑', quantity: `送剪原片 ${item.units} 条` }
  }
  const category = item.category || item.kind
  const label = categories[category] ?? categories[item.kind] ?? '其他用量'
  const quantity = item.kind === 'VIDEO'
    ? item.units === 1 && !item.category ? '时长未记录' : `${item.units} 秒`
    : item.kind === 'IMAGE' ? `${item.units} 张` : `${item.units} 单位`
  return { label, quantity }
}
