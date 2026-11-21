import type { CutflowHandoff, MultiCutflowTask } from '../api'

// Confirmed stages only: the backend does not report a render percentage.
export function cutflowPresentation(handoff: CutflowHandoff) {
  const stages: Record<string, { label: string; step: number; active: boolean }> = {
    QUEUED: { label: '等待传输', step: 0, active: true },
    READY: { label: '素材已准备，等待导入', step: 1, active: true },
    IMPORTING: { label: '剪辑端导入中', step: 2, active: true },
    AWAITING_EDIT: { label: handoff.editMode === 'automatic' ? '等待自动剪辑' : '等待剪辑配置', step: 3, active: true },
    EDITING: { label: '正在剪辑', step: 4, active: true },
    PENDING_REVIEW: handoff.outputs.length
      ? { label: '剪辑完成', step: 5, active: false }
      : { label: '等待成片回传', step: 4, active: true },
    FAILED: { label: '剪辑交接失败', step: 0, active: false },
    CANCELED: { label: '剪辑已取消', step: 0, active: false },
  }
  return stages[handoff.status] ?? { label: '剪辑状态待确认', step: 0, active: false }
}

const ACTIVE_MULTI_CUTFLOW_STATUSES = new Set([
  'QUEUED',
  'READY',
  'ACCEPTED',
  'IMPORTING',
  'EDITING',
  'UNKNOWN',
])

export function multiCutflowPresentation(task: MultiCutflowTask) {
  if (task.status === 'READY' && task.errorCode === 'CUTFLOW_NOTIFICATION_TOO_LARGE') {
    return { label: 'CUT 通知超限，等待 NAS 接单', active: true, completed: false }
  }
  if (task.status === 'READY' && task.errorCode === 'CUTFLOW_STATUS_MISSING') {
    return { label: '等待 CUT 接单超时，正在核对', active: true, completed: false }
  }
  if (task.status === 'READY' && task.errorCode === 'CUTFLOW_IMPORT_REJECTED') {
    return { label: 'CUT 拒绝接单，等待排查', active: true, completed: false }
  }
  if (task.status === 'QUEUED') {
    return { label: '待送剪，正在准备素材', active: true, completed: false }
  }
  if (task.status === 'READY') {
    return { label: '素材已就绪，等待 CUT 接单', active: true, completed: false }
  }
  if (task.status === 'ACCEPTED') {
    return { label: 'CUT 已接单，排队中', active: true, completed: false }
  }
  if (task.status === 'IMPORTING') {
    return { label: 'CUT 已接单，正在导入', active: true, completed: false }
  }
  if (task.status === 'EDITING') {
    return { label: 'CUT 正在剪辑', active: true, completed: false }
  }
  if (task.status === 'PENDING_REVIEW') {
    return task.outputs.length
      ? { label: '成片待审核', active: false, completed: true }
      : { label: '等待成片回传', active: true, completed: false }
  }
  if (task.status === 'FAILED') {
    return { label: '剪辑失败', active: false, completed: false }
  }
  if (task.status === 'CANCELED') {
    return { label: '剪辑已取消', active: false, completed: false }
  }
  return {
    label: '状态待确认',
    active: ACTIVE_MULTI_CUTFLOW_STATUSES.has(task.status),
    completed: false,
  }
}

export function cutflowReviewLabel(status: string) {
  return ({ PENDING: '待审核', APPROVED: '审核通过', REJECTED: '审核未通过' } as Record<string, string>)[status] ?? '审核状态待确认'
}

export function cutflowStageLabel(stage: string | null) {
  const labels: Record<string, string> = {
    importing: '导入素材', uploading: '已接收素材', reference_queued: '等待参考图分析',
    analyzing_reference: '分析参考图', creating_proxies: '准备预览素材',
    detecting_products: '识别商品', regroup_queued: '等待素材分组', reference_ready: '参考图已就绪',
    batch_queued: '等待剪辑', editing: '剪辑处理中', quality: '检查素材质量', analyze: '分析素材',
    semantic: '分析镜头', clip: '编排镜头', render: '渲染成片', review: '成片待审核',
    review_ready: '成片待审核', completed: '成片已发布', failed: '剪辑端处理失败',
    canceled: '已取消', cancel_requested: '正在取消', revision_queued: '等待修改', revising: '正在修改',
  }
  return (stage && labels[stage]) || '尚未获得阶段信息'
}

export function cutflowRecoveryHint(code: string | null | undefined) {
  const hints: Record<string, string> = {
    CUTFLOW_MEDIA_UNAVAILABLE: '等待共享文件恢复后自动回收，无需再次送入剪辑。',
    CUTFLOW_RECEIPT_MISSING: '正在查询原交付，请检查剪辑端是否恢复了接单记录。',
    CUTFLOW_SCOPE_MISMATCH: '请检查剪辑端处理范围是否包含此交付。',
    CUTFLOW_WORKER_OFFLINE: '等待剪辑端处理进程恢复后继续。',
    CUTFLOW_IMPORT_STALLED: '已接单但导入未推进，请检查剪辑端导入进程和共享文件。',
    CUTFLOW_PROGRESS_STALLED: '阶段长时间未推进，请检查剪辑端运行状态。',
    CUTFLOW_BUDGET_REQUIRED: '请在剪辑端确认本次费用授权。',
  }
  return (code && hints[code]) || ''
}

export function cutflowTime(value: string | null) {
  if (!value) return '暂无记录'
  const date = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value.replace(' ', 'T')}Z`)
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  }).format(date) : '暂无记录'
}
