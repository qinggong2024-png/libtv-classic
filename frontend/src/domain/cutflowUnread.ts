import type { MultiCutflowTask } from '../api'

/** Only an explicitly unread *latest* delivered output warrants a task-card dot. */
export function hasUnseenCutflowOutput(batches: MultiCutflowTask[], loaded: boolean, error = false) {
  if (!loaded || error) return false
  const latest = [...batches].filter(batch => batch.outputs.length > 0)
    .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))[0]
  return latest?.outputs[0]?.viewedAt === null
}
