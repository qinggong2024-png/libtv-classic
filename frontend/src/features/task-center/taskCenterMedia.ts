import type { MultiCutflowTask } from '../../api'
import type { Task, Variant } from '../../types'
import type { taskCenterState } from '../../domain/taskCenterState'

export type PlayableMedia =
  | { kind: 'final'; key: string; label: string; mediaUrl: string; createdAt: string; batchId: string; outputId: string; viewedAt?: string | null }
  | { kind: 'original'; key: string; label: string; variant: Variant; artifactId: string }

export function taskPlayableMedia(task: Task, batches: MultiCutflowTask[]): PlayableMedia[] {
  // The CUT output list is authoritative; a queued/acknowledged job is not an output.
  const finals = batches.flatMap(batch => batch.outputs.map(output => ({
    kind: 'final' as const,
    key: 'final:' + batch.id + ':' + output.id,
    batchId: batch.id,
    outputId: output.id,
    viewedAt: output.viewedAt,
    label: '最终成片',
    mediaUrl: output.mediaUrl,
    createdAt: output.createdAt || batch.updatedAt,
  }))).filter(item => Boolean(item.mediaUrl))
    .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))
  const originals = task.variants.flatMap((variant, index) => variant.downloadArtifactId ? [{
    kind: 'original' as const,
    key: 'original:' + variant.id,
    label: '原片 ' + (index + 1),
    variant,
    artifactId: variant.downloadArtifactId,
  }] : [])
  // Show the latest verified CUT delivery, but keep every successful original available.
  return [...finals.slice(0, 1), ...originals]
}

export type CutCoverStatus = { label: string; busy: boolean }

// Reuse the task center's resolved workflow state; do not infer processing from a raw file.
export function taskCutCoverStatus(
  media: PlayableMedia | null,
  state: Pick<ReturnType<typeof taskCenterState>, 'group' | 'label' | 'manualSelection' | 'readyCount'>,
  totalOriginals: number,
): CutCoverStatus | null {
  if (!media) return null
  if (state.group === 'active') {
    // A partial raw result is only an intermediate preview while the remaining
    // originals are still in flight. Recovery/unknown states keep their own copy.
    if (media.kind === 'original' && state.readyCount > 0 && state.readyCount < totalOriginals) {
      const rawStages: Record<string, string> = {
        '正在生成原片': '正在生成原片',
        '等待生成': '等待生成原片',
        '正在准备生成': '正在准备生成',
        '正在准备提交': '正在准备提交',
        '正在下载成品': '正在下载原片',
        '正在校验成品': '正在校验原片',
      }
      const stage = rawStages[state.label]
      if (stage) return { label: `${stage} ${state.readyCount}/${totalOriginals}`, busy: true }
    }
    const waiting = ['等待自动送剪', '待送剪，正在准备素材', '素材已就绪，等待 CUT 接单', 'CUT 已接单，排队中', 'CUT 已接单，正在导入'].includes(state.label)
    const editing = state.label === 'CUT 正在剪辑'
    const returning = state.label === '等待成片回传'
    if (waiting || editing || returning) {
      return { label: media.kind === 'final' ? '重新剪辑中' : waiting ? '等待剪辑' : returning ? '等待成片回传' : '正在剪辑中', busy: true }
    }
  }
  if (media.kind === 'original' && state.group === 'attention') {
    if (state.manualSelection) return { label: '等待送剪', busy: false }
    if (state.label === '剪辑需要处理') return { label: '剪辑异常', busy: false }
  }
  return null
}

export function taskPrimaryMedia(media: PlayableMedia[]): PlayableMedia | null {
  return media[0] ?? null
}
