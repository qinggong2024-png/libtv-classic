import type { Task } from '../../types'

export function depthVideoHistoryEntries(tasks: Task[]) {
  return tasks.flatMap((task) => {
    if (task.workflowKind !== 'DEEP_VIDEO') return []
    const downloadId = task.depthVideoResult?.downloadId ?? (
      task.settings?.deepVideoMode === 'depth'
        ? task.variants.find((variant) => variant.downloadArtifactId)?.downloadArtifactId
        : null
    )
    return downloadId ? [{ task, downloadId }] : []
  })
}
