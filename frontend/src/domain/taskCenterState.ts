import type { MultiCutflowTask } from '../api'
import type { Task } from '../types'
import { ACTIVE_PHASES, AWAITING_USER_INPUT_PHASES, TASK_PHASE_META } from './workflowStates'
import { activeTaskRecoveryPresentation } from './statusPresentation'
import { multiCutflowPresentation, nasRecoveryMessage } from './cutflowPresentation'
import { autoCutflowWaitDelayed, autoCutflowWaitingCopy } from './autoCutflowWaiting'

// This is a *user-facing* workflow state, not a mutation of backend generation phases.
// The original video being ready does not mean the finished ad is ready.
export function taskCenterState(task: Task, batches: MultiCutflowTask[], status: { loaded?: boolean; error?: boolean; now?: number } = {}) {
  const readyCount = task.variants.filter(variant => Boolean(variant.downloadArtifactId)).length
  const matching = batches.filter(batch => batch.sources.some(source => source.taskId === task.id))
  const outputs = matching.reduce((count, batch) => count + batch.outputs.length, 0)
  const recent = [...matching].sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))
  const active = recent.find(batch => multiCutflowPresentation(batch).active)
  const failed = recent.length > 0 && ['FAILED', 'CANCELED'].includes(recent[0].status) && !recent[0].outputs.length
  const manual = task.settings?.autoSubmitToCutflow === false
  const unknown = task.variants.some(variant => variant.generationStatus === 'UNKNOWN')
  const manualSelection = readyCount > 0 && !matching.length && manual

  if (task.phase === 'CANCELLED') return { group: 'cancelled', label: '已取消', detail: '任务已取消', outputs, readyCount, manualSelection: false } as const
  if (AWAITING_USER_INPUT_PHASES.has(task.phase) || unknown) {
    return { group: 'attention', label: '需要处理', detail: '打开任务查看处理方式', outputs, readyCount, manualSelection: false } as const
  }
  if (active) {
    const intakeWarning = active.status === 'READY' && [
      'CUTFLOW_NOTIFICATION_TOO_LARGE', 'CUTFLOW_STATUS_MISSING', 'CUTFLOW_IMPORT_REJECTED',
    ].includes(active.errorCode ?? '')
    const nas = nasRecoveryMessage(active.errorCode)
    return {
      group: intakeWarning ? 'attention' : 'active',
      label: multiCutflowPresentation(active).label,
      detail: nas ? '原任务已保留，恢复连接后自动继续，无需重新送剪。' : intakeWarning ? '剪辑接单尚未确认，原片已保留，无需重复送剪；若持续未推进请联系管理员。' : active.status === 'QUEUED' ? '正在准备送剪，无需操作' : '无需重复送剪',
      outputs, readyCount, manualSelection: false,
    } as const
  }
  if (failed) {
    return { group: 'attention', label: '剪辑需要处理', detail: '查看剪辑记录与处理建议', outputs, readyCount, manualSelection: false } as const
  }
  if (outputs > 0) {
    return { group: 'done', label: '成片可查看', detail: `已回传 ${outputs} 条成片`, outputs, readyCount, manualSelection: false } as const
  }
  // An unfinished raw video still needs generation to finish before a cut.
  const recovery = ACTIVE_PHASES.has(task.phase) ? activeTaskRecoveryPresentation(task) : null
  const rawStageLabel = task.phase === 'GENERATING' ? '正在生成原片' : (TASK_PHASE_META[task.phase]?.label ?? '正在生成原片')
  if (ACTIVE_PHASES.has(task.phase) && readyCount < task.variants.length) {
    return {
      group: 'active',
      label: recovery?.label ?? rawStageLabel,
      detail: recovery?.description ?? `原片 ${readyCount}/${task.variants.length}`,
      outputs, readyCount, manualSelection: false,
    } as const
  }
  if (readyCount && status.error) {
    return { group: 'attention', label: '剪辑状态同步异常', detail: '无法确认 CUT 最新状态；请刷新核实，避免重复送剪', outputs, readyCount, manualSelection: false } as const
  }
  if (readyCount && status.loaded === false) {
    return { group: 'active', label: '正在核对剪辑状态', detail: '尚未确认是否送剪，无需重复操作', outputs, readyCount, manualSelection: false } as const
  }
  if (manualSelection) {
    return { group: 'attention', label: '等待手动送剪', detail: '选择原片送入剪辑', outputs, readyCount, manualSelection: true } as const
  }
  if (readyCount) {
    if (task.settings?.autoSubmitToCutflow === true && !matching.length) {
      const delayed = autoCutflowWaitDelayed(task, status.now ?? Date.now())
      return { group: delayed ? 'attention' : 'active', ...autoCutflowWaitingCopy(delayed), outputs, readyCount, manualSelection: false } as const
    }
    return { group: 'attention', label: task.settings?.autoSubmitToCutflow === true ? '自动送剪未启动' : '原片待处理', detail: task.settings?.autoSubmitToCutflow === true ? '未查到剪辑任务；打开详情查看并刷新' : '查看详情选择送剪方式', outputs, readyCount, manualSelection: false } as const
  }
  if (ACTIVE_PHASES.has(task.phase)) {
    return {
      group: 'active',
      label: recovery?.label ?? rawStageLabel,
      detail: recovery?.description ?? `原片 ${readyCount}/${task.variants.length}`,
      outputs, readyCount, manualSelection: false,
    } as const
  }
  return { group: 'attention', label: '请查看任务', detail: '尚未获得可用原片', outputs, readyCount, manualSelection: false } as const
}
