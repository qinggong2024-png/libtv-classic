import type { Task } from '../../types'
import { nasRecoveryMessage } from '../../domain/cutflowPresentation'
import type { MultiCutflowTask, AutoCutflowDispatchStatus } from '../../api'
import { autoCutflowWaitingCopy } from '../../domain/autoCutflowWaiting'

export type CutflowNextAction = 'select' | 'refresh' | 'history' | 'view' | null
export type CutflowTone = 'neutral' | 'attention' | 'success'

export interface CutflowDispatchPresentation {
  modeLabel: string
  stageLabel: string
  headline: string
  detail: string
  actionLabel: string | null
  action: CutflowNextAction
  tone: CutflowTone
  waiting?: boolean
}

type EditSnapshot = Pick<MultiCutflowTask, 'status' | 'errorCode' | 'errorMessage'>

/**
 * Only a persisted edit record proves a local CUT submission was created.
 * "Originals ready" is not evidence that CUT received the sources.
 */
export function cutflowDispatchPresentation(
  settings: Task['settings'], hasEditRecord: boolean, hasActiveEdit: boolean,
  hasDeliveredOutput: boolean, hasFailedEdit = false, statusLoaded = true,
  statusError = false, waitDelayed = false, latestEdit: EditSnapshot | null = null,
  autoStatus: AutoCutflowDispatchStatus | null = null,
): CutflowDispatchPresentation {
  const choice = settings?.autoSubmitToCutflow
  const modeLabel = choice === true ? '自动送剪' : choice === false ? '手动送剪' : '送剪方式未知'
  const result = (
    headline: string, detail: string, stageLabel: string,
    action: CutflowNextAction = null, actionLabel: string | null = null, tone: CutflowTone = 'neutral',
  ): CutflowDispatchPresentation => ({ modeLabel, headline, detail, stageLabel, action, actionLabel, tone })

  // A failed refresh must not make cached stages appear current or confirmed.
  if (statusError) return result('剪辑状态同步异常', '暂时无法获取最新剪辑进度。原任务记录仍保留，请刷新状态；若持续无法查询，请联系管理员，不要重复送剪。', '待核实', 'refresh', '重新查询', 'attention')
  if (!statusLoaded) return { ...result('正在核对剪辑状态', '正在查询是否已经建立剪辑任务。', '查询中'), waiting: true }

  if (hasFailedEdit) return result('剪辑任务失败', '原片仍可查看。请查看失败原因；联系管理员确认能否恢复原剪辑任务后再决定是否重试。', '处理失败', 'history', '查看原因', 'attention')
  if (hasActiveEdit) {
    const nas = nasRecoveryMessage(latestEdit?.errorCode)
    if (nas) return result(nas, '原任务与成片记录已保留，恢复连接后自动继续。无需重复送剪；长时间未恢复请联系管理员。', '连接待恢复', null, null, 'neutral')
    if (latestEdit?.status === 'QUEUED') return result('待送剪，正在准备素材', 'ALLWE 已保存送剪任务，后台将打包并投递原片；尚未确认 CUT 接单。', '待投递')
    if (latestEdit?.status === 'READY') {
      if (latestEdit.errorCode === 'CUTFLOW_NOTIFICATION_TOO_LARGE') return result('等待 CUT 接单', '素材已准备，正在等待剪辑服务接收，无需重复送剪。如果长时间未推进，请联系管理员。', '接单待核实', 'history', '查看记录', 'attention')
      if (latestEdit.errorCode === 'CUTFLOW_STATUS_MISSING' || latestEdit.errorCode === 'CUTFLOW_IMPORT_REJECTED')
        return result('CUT 接单异常', '原片已保存，剪辑服务尚未确认接收。请联系管理员核查原任务，不要重复送剪。', '接单待核实', 'history', '查看记录', 'attention')
      return result('素材已送出，等待 CUT 接单', '素材包已就绪，正在等待 CUT 确认接收；尚不能算剪辑中。', '等待接单')
    }
    if (latestEdit?.status === 'ACCEPTED') return result('CUT 已接单，排队中', 'CUT 已确认接单，正在等待可用剪辑资源；不需要再次送剪。', 'CUT 排队中')
    if (latestEdit?.status === 'IMPORTING') return result('CUT 已接单，正在导入', '剪辑端已开始导入素材，等待进入剪辑流程。', '导入中')
    if (latestEdit?.status === 'EDITING') return result('CUT 正在剪辑', '剪辑端已进入执行阶段，完成后会回传成片。', '剪辑中')
    if (latestEdit?.status === 'UNKNOWN') return result('CUT 状态待核实', '已有送剪记录，暂时无法确认剪辑进度。请刷新状态，持续未更新时联系管理员；不要重新送剪。', '状态待核实', 'refresh', '重新查询', 'attention')
    if (latestEdit?.status === 'PENDING_REVIEW') return result('等待成片回传', '剪辑流程已接近完成，但还没有可用的成片记录。', '等待回传')
    return result('已建立剪辑任务，正在处理', '已找到本地剪辑记录，具体阶段请查看下方剪辑记录。', '处理中')
  }
  if (hasDeliveredOutput) return result('成片已完成', '现在可以查看或下载。', '已完成', 'view', '查看成片', 'success')
  if (hasEditRecord) return result('剪辑任务待核实', '已存在历史剪辑任务，但没有确认的进行中阶段；请查看记录，不要直接重复送剪。', '待核实', 'history', '查看记录', 'attention')
  if (choice === true) {
    if (autoStatus?.state === 'BLOCKED') {
      if (autoStatus.reasonCode === 'USD_DAILY_BUDGET_EXCEEDED') {
        const required = autoStatus.requiredUsd == null ? '待核实' : `$${autoStatus.requiredUsd.toFixed(2)}`
        const remaining = autoStatus.remainingUsd == null ? '待核实' : `$${autoStatus.remainingUsd.toFixed(2)}`
        return result('自动送剪受预算限制', `今日剩余额度 ${remaining}，本次剪辑预计需 ${required}。原片已保留，请联系管理员调整预算；无需重新生成原片。`, '预算不足', 'refresh', '刷新状态', 'attention')
      }
      return result('自动送剪条件未满足', autoStatus.reasonCode === 'NO_USABLE_ORIGINALS' ? '没有通过校验的原片，请查看失败片段并按原片处理建议操作。' : autoStatus.reasonCode === 'ACCOUNT_NOT_ELIGIBLE' ? '当前账号没有送剪权限或额度，请联系管理员处理原任务。' : '原片送剪条件未通过，请联系管理员检查原因并处理原任务，无需重新生成。', '待核查', 'refresh', '刷新状态', 'attention')
    }
    if (autoStatus?.state === 'WORKER_UNAVAILABLE')
      return result('自动送剪服务待恢复', '原片已保存，剪辑服务暂未就绪。请联系管理员恢复服务，无需重新生成或重复送剪。', '服务待恢复', 'refresh', '刷新状态', 'attention')
    if (autoStatus?.state === 'RECORDED')
      return result('已建立剪辑任务，等待同步', '已保存剪辑任务，正在同步最新进度，无需重复送剪。', '同步中', 'refresh', '刷新状态', 'attention')
    const copy = autoCutflowWaitingCopy(waitDelayed)
    return { ...result(copy.label, copy.detail, '等待自动送剪', waitDelayed ? 'refresh' : null, waitDelayed ? '刷新状态' : null), waiting: true }
  }
  if (choice === false) return result('等待你送剪', '原片已完成，选择原片后即可送剪。', '未开始', 'select', '选择原片送剪', 'attention')
  return result('还没有送剪', '无法确认当时的自动送剪设置，请手动选择原片。', '未开始', 'select', '选择原片送剪', 'attention')
}
