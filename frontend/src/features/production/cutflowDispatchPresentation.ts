import type { Task } from '../../types'
import type { MultiCutflowTask, AutoCutflowDispatchStatus } from '../../api'

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
  if (statusError) return result('剪辑状态同步异常', '无法读取最新剪辑记录，当前不能确认 CUT 是否接单或已完成；请重新查询，避免重复送剪。', '待核实', 'refresh', '重新查询', 'attention')
  if (!statusLoaded) return { ...result('正在核对剪辑状态', '正在查询是否已经建立剪辑任务。', '查询中'), waiting: true }

  if (hasFailedEdit) return result('剪辑任务失败', '已找到失败的剪辑记录；请查看错误原因。不要直接重复创建新任务。', '处理失败', 'history', '查看原因', 'attention')
  if (hasActiveEdit) {
    if (latestEdit?.status === 'QUEUED') return result('待送剪，正在准备素材', 'ALLWE 已保存送剪任务，后台将打包并投递原片；尚未确认 CUT 接单。', '待投递')
    if (latestEdit?.status === 'READY') {
      if (latestEdit.errorCode === 'CUTFLOW_NOTIFICATION_TOO_LARGE') return result('等待 CUT 接单', '通知超限，已切换等待 NAS 扫描接单；请核对共享目录与 CUT 接单状态。', '接单待核实', 'history', '查看记录', 'attention')
      if (latestEdit.errorCode === 'CUTFLOW_STATUS_MISSING' || latestEdit.errorCode === 'CUTFLOW_IMPORT_REJECTED')
        return result('CUT 接单异常', '素材包已创建，但 CUT 接单尚未确认，请检查剪辑记录；不要重复送剪。', '接单待核实', 'history', '查看记录', 'attention')
      return result('素材已送出，等待 CUT 接单', '素材包已就绪，正在等待 CUT 确认接收；尚不能算剪辑中。', '等待接单')
    }
    if (latestEdit?.status === 'ACCEPTED') return result('CUT 已接单，排队中', 'CUT 已确认接单，正在等待可用剪辑资源；不需要再次送剪。', 'CUT 排队中')
    if (latestEdit?.status === 'IMPORTING') return result('CUT 已接单，正在导入', '剪辑端已开始导入素材，等待进入剪辑流程。', '导入中')
    if (latestEdit?.status === 'EDITING') return result('CUT 正在剪辑', '剪辑端已进入执行阶段，完成后会回传成片。', '剪辑中')
    if (latestEdit?.status === 'UNKNOWN') return result('CUT 状态待核实', '本地已有剪辑任务，但远端阶段尚未确认；请查询原任务，不要重新送剪。', '状态待核实', 'refresh', '重新查询', 'attention')
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
        return result('自动送剪受预算限制', `今日剩余额度 ${remaining}，本次剪辑预计需 ${required}。ALLWE 尚未创建 CUT 任务，请核对预算。`, '预算不足', 'refresh', '刷新状态', 'attention')
      }
      return result('自动送剪条件未满足', autoStatus.message || '本次送剪未通过条件检查，尚未创建剪辑任务。', '待核查', 'refresh', '刷新状态', 'attention')
    }
    if (autoStatus?.state === 'WORKER_UNAVAILABLE')
      return result('自动送剪服务待恢复', '自动送剪调度进程状态异常，尚未确认创建 CUT 任务。', '服务待恢复', 'refresh', '刷新状态', 'attention')
    if (autoStatus?.state === 'RECORDED')
      return result('已建立剪辑任务，等待同步', 'ALLWE 已记录 CUT 剪辑任务，但任务列表还没有同步到对应记录；请刷新状态。', '同步中', 'refresh', '刷新状态', 'attention')
    if (waitDelayed) return result('自动送剪任务尚未建立', '原片已就绪超过 3 分钟，但 ALLWE 尚无剪辑任务记录。请检查自动调度，不能据此认定 CUT 已接单。', '待调度核查', 'refresh', '重新核查', 'attention')
    return { ...result('等待创建送剪任务', '原片就绪后，系统将尝试创建剪辑任务；当前尚未确认送达 CUT。', '等待调度', 'refresh', '刷新状态'), waiting: true }
  }
  if (choice === false) return result('等待你送剪', '原片已完成，选择原片后即可送剪。', '未开始', 'select', '选择原片送剪', 'attention')
  return result('还没有送剪', '无法确认当时的自动送剪设置，请手动选择原片。', '未开始', 'select', '选择原片送剪', 'attention')
}
