import type { MultiCutflowTask } from '../api'
import type { Task } from '../types'
import { ACTIVE_PHASES, AWAITING_USER_INPUT_PHASES } from './workflowStates'
import { multiCutflowPresentation } from './cutflowPresentation'
import { autoCutflowWaitDelayed } from './autoCutflowWaiting'

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
    return {
      group: intakeWarning ? 'attention' : 'active',
      label: multiCutflowPresentation(active).label,
      detail: intakeWarning ? (active.errorMessage || 'CUT 尚未确认接单；请核对共享目录扫描，不要重复送剪') : '已送剪，无需重复操作',
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
  if (ACTIVE_PHASES.has(task.phase) && readyCount < task.variants.length) {
    return { group: 'active', label: '正在生成原片', detail: `原片 ${readyCount}/${task.variants.length}`, outputs, readyCount, manualSelection: false } as const
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
      return { group: delayed ? 'attention' : 'active', label: delayed ? '送剪任务尚未建立' : '等待创建送剪任务', detail: delayed ? '原片已就绪超过3分钟，尚无本地剪辑记录；请检查自动调度' : '等待系统建立剪辑记录；尚未确认 CUT 接单', outputs, readyCount, manualSelection: false } as const
    }
    return { group: 'attention', label: task.settings?.autoSubmitToCutflow === true ? '自动送剪未启动' : '原片待处理', detail: task.settings?.autoSubmitToCutflow === true ? '未查到剪辑任务；打开详情查看并刷新' : '查看详情选择送剪方式', outputs, readyCount, manualSelection: false } as const
  }
  if (ACTIVE_PHASES.has(task.phase)) {
    return { group: 'active', label: '正在生成原片', detail: `原片 ${readyCount}/${task.variants.length}`, outputs, readyCount, manualSelection: false } as const
  }
  return { group: 'attention', label: '请查看任务', detail: '尚未获得可用原片', outputs, readyCount, manualSelection: false } as const
}
