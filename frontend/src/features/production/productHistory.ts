import type { Asset, Task } from '../../types'

export type ProductHistoryEntry = { task: Task; asset: Asset; key: string }

export function productHistoryEntries(tasks: readonly Task[], siteProfileId: string, query = ''): ProductHistoryEntry[] {
  if (!siteProfileId) return []
  const seen = new Set<string>()
  const search = query.trim().toLowerCase()
  const result: ProductHistoryEntry[] = []
  for (const task of [...tasks].sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.id.localeCompare(a.id))) {
    if (task.siteProfileId !== siteProfileId) continue
    for (const asset of [...task.assets].sort((a, b) => a.order - b.order)) {
      if (asset.role !== 'PRODUCT' || !/\.(jpe?g|png|webp)$/i.test(asset.originalFilename) || !asset.id) continue
      if (search && ![asset.originalFilename, task.title].join(' ').toLowerCase().includes(search)) continue
      const key = asset.sha256 || asset.id
      if (seen.has(key)) continue
      seen.add(key)
      result.push({ task, asset, key })
    }
  }
  return result
}

export async function sameReferenceFile(a: File, b: File): Promise<boolean> {
  if (a.size !== b.size) return false
  const [left, right] = await Promise.all([a.arrayBuffer(), b.arrayBuffer()])
  const x = new Uint8Array(left), y = new Uint8Array(right)
  return x.every((byte, index) => byte === y[index])
}
