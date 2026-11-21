// 多源剪辑篮的状态与提交逻辑。
// 交互对齐「图片中心 → 选片工作台 → 送入视频队列」：顺序即输入顺序，拖拽插入排序，
// 不做二次人工确认。校验规则来自 cutflowLibraryModel，未放宽。
//
// 所有新送剪都进入同一 sources[] 合同；单条只是数组长度 1。
import { useEffect, useMemo, useRef, useState } from 'react'
import { createMultiCutflowTask, listCutflowHandoffs, listMultiCutflowTasks, retryCutflowHandoff } from '../../api'
import type { MultiCutflowTask } from '../../api'
import type { Task } from '../../types'
import { multiCutflowPresentation } from '../../domain/cutflowPresentation'
import { autoCutflowCandidate, autoCutflowPollDelay, type AutoCutflowCandidate } from '../../domain/autoCutflowWaiting'
import {
  cutflowBrandConflict,
  cutflowSourceCanJoinBasket,
  cutflowSourceSummary,
  cutflowSources,
  cutflowUnverifiedBrandCount,
  MAX_CUTFLOW_SOURCES,
  type CutflowSourceState,
} from './cutflowLibraryModel'

// 剪辑按「每条原片」计价，单价由管理后台「全站生成单价 → 每条剪辑预估」维护。
// 预估金额不走这里自算，统一由按钮内的 GenerationCostBadge 走全站预算上下文，
// 与生图 / 生视频的提交按钮同一口径，避免两处各取一次价。

const STORAGE_KEY = 'cutflow-multi-source-selection'

const createAttemptKey = () => (
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
)

function readStoredSelection(storageKey: string): string[] {
  try {
    const saved = sessionStorage.getItem(storageKey)
    const parsed: unknown = saved ? JSON.parse(saved) : []
    return Array.isArray(parsed) && parsed.every(id => typeof id === 'string')
      ? parsed.slice(0, MAX_CUTFLOW_SOURCES)
      : []
  } catch {
    return []
  }
}

export function useMultiCutBasket({ tasks, canWrite, enabled, storageKey = STORAGE_KEY, onSubmitted }: {
  tasks: Task[]; canWrite: boolean; enabled: boolean; storageKey?: string
  onSubmitted?: () => void | Promise<void>
}) {
  const sources = useMemo(() => cutflowSources(tasks), [tasks])
  const [selection, setSelection] = useState<string[]>(() => readStoredSelection(storageKey))
  const [states, setStates] = useState<Record<string, CutflowSourceState>>({})
  const [multiTasks, setMultiTasks] = useState<MultiCutflowTask[]>([])
  const [multiTasksLoaded, setMultiTasksLoaded] = useState(false)
  const [multiTaskError, setMultiTaskError] = useState('')
  const [multiTasksCheckedAt, setMultiTasksCheckedAt] = useState(0)
  const [reading, setReading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const quickSubmitGuard = useRef(false)
  const [message, setMessage] = useState('')
  const [refreshToken, setRefreshToken] = useState(0)

  useEffect(() => {
    try { sessionStorage.setItem(storageKey, JSON.stringify(selection)) } catch { /* session storage may be unavailable */ }
  }, [selection, storageKey])

  // 只读取已选中的原片：任务页可能有几十条原片，全量轮询没有必要，
  // 而送剪前的合法性判断只关心被选中的那几条。
  const selectionKey = JSON.stringify(selection)
  const autoCandidatesKey = JSON.stringify(enabled ? tasks.flatMap(task => {
    const candidate = autoCutflowCandidate(task)
    return candidate ? [candidate] : []
  }) : [])
  useEffect(() => {
    const ids = JSON.parse(selectionKey) as string[]
    const autoCandidates = JSON.parse(autoCandidatesKey) as AutoCutflowCandidate[]
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    setReading(ids.length > 0)
    setMultiTasksLoaded(false)
    const refresh = async () => {
      let nextIndex = 0
      let active = false
      let multiReadOk = true
      let observedBatches: MultiCutflowTask[] = []
      const multiRead = listMultiCutflowTasks(controller.signal).then(result => {
        if (controller.signal.aborted) return
        observedBatches = result.tasks
        setMultiTasks(result.tasks)
        setMultiTaskError('')
        active ||= result.tasks.some(task => multiCutflowPresentation(task).active)
      }).catch(cause => {
        if (!controller.signal.aborted) {
          multiReadOk = false
          setMultiTaskError(cause instanceof Error ? cause.message : '剪辑任务状态读取失败')
        }
      })
      await Promise.all([
        multiRead,
        ...Array.from({ length: Math.min(ids.length, 6) }, async () => {
          while (nextIndex < ids.length && !controller.signal.aborted) {
            const id = ids[nextIndex++]
            try {
              const result = await listCutflowHandoffs(id, controller.signal)
              if (controller.signal.aborted) return
              setStates(previous => ({ ...previous, [id]: result }))
              active ||= result.handoffs.some(handoff => (
                ['QUEUED', 'READY', 'IMPORTING', 'EDITING'].includes(handoff.status)
              ))
            } catch (cause) {
              if (!controller.signal.aborted) {
                setStates(previous => ({
                  ...previous,
                  [id]: {
                    enabled: previous[id]?.enabled ?? true,
                    handoffs: previous[id]?.handoffs ?? [],
                    error: cause instanceof Error ? cause.message : '剪辑状态读取失败',
                  },
                }))
              }
            }
          }
        }),
      ])
      if (!controller.signal.aborted) {
        const checkedAt = Date.now()
        setMultiTasksCheckedAt(checkedAt)
        setMultiTasksLoaded(multiReadOk)
        setReading(false)
        timer = setTimeout(() => void refresh(), autoCutflowPollDelay(autoCandidates, observedBatches, checkedAt, active, !multiReadOk))
      }
    }
    void refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [selectionKey, refreshToken, autoCandidatesKey])

  const entries = selection
    .map(id => sources.find(source => source.variant.id === id))
    .filter((source): source is NonNullable<typeof source> => Boolean(source))
  const activeSingleStatuses = new Set(['QUEUED', 'READY', 'IMPORTING', 'EDITING', 'AWAITING_EDIT'])
  const multiTaskByVariant = new Map<string, MultiCutflowTask>()
  const activeMultiTaskByVariant = new Map<string, MultiCutflowTask>()
  for (const task of multiTasks) {
    for (const source of task.sources) {
      if (!multiTaskByVariant.has(source.variantId)) multiTaskByVariant.set(source.variantId, task)
      if (multiCutflowPresentation(task).active && !activeMultiTaskByVariant.has(source.variantId)) {
        activeMultiTaskByVariant.set(source.variantId, task)
      }
    }
  }
  const awaitingAutoVariantIds = new Set(tasks.filter(task => autoCutflowCandidate(task)
    && !task.variants.some(variant => multiTaskByVariant.has(variant.id) || states[variant.id]?.handoffs.length))
    .flatMap(task => task.variants.map(variant => variant.id)))
  const known = entries.map(source => {
    const state = states[source.variant.id]
    const existingMultiTask = multiTaskByVariant.get(source.variant.id) ?? null
    return {
      ...source,
      state,
      ...cutflowSourceSummary(source, state),
      existingMultiTask,
      eligible: cutflowSourceCanJoinBasket(state, source.artifactId)
        && !activeMultiTaskByVariant.has(source.variant.id),
    }
  })
  const validIds = new Set(known.filter(entry => entry.eligible).map(entry => entry.variant.id))
  const blocked = known.filter(entry => entry.state && !entry.eligible)
  const brandConflict = cutflowBrandConflict(entries)
  // 缺品牌不再算冲突：只提示，不参与 canSubmit。服务端仍会核验。
  const unverifiedBrands = cutflowUnverifiedBrandCount(entries)
  const pendingReads = entries.some(entry => !states[entry.variant.id]) || !multiTasksLoaded
  const add = (sourceId: string) => {
    if (!selection.includes(sourceId) && selection.length >= MAX_CUTFLOW_SOURCES) {
      setMessage(`每次最多选择 ${MAX_CUTFLOW_SOURCES} 条原片`)
      return
    }
    setSelection(previous => (
      previous.includes(sourceId) ? previous : [...previous, sourceId]
    ))
  }
  const remove = (sourceId: string) => setSelection(previous => previous.filter(id => id !== sourceId))
  const move = (sourceId: string, direction: -1 | 1) => setSelection(previous => {
    const index = previous.indexOf(sourceId)
    const target = index + direction
    if (index < 0 || target < 0 || target >= previous.length) return previous
    const next = [...previous]
    ;[next[index], next[target]] = [next[target], next[index]]
    return next
  })
  // 卡片上的「送入剪辑 / 已加入剪辑篮」按同一个按钮切换，避免再引入一个独立的勾选控件。
  const toggle = (sourceId: string) => {
    if (!selection.includes(sourceId) && selection.length >= MAX_CUTFLOW_SOURCES) {
      setMessage(`每次最多选择 ${MAX_CUTFLOW_SOURCES} 条原片`)
      return
    }
    setSelection(previous => (
      previous.includes(sourceId) ? previous.filter(id => id !== sourceId) : [...previous, sourceId]
    ))
  }
  // 拖拽插入：放到目标原片之前；放到空白区域则追加到末尾。与图片选片工作台一致。
  const place = (sourceId: string, beforeSourceId?: string) => setSelection(previous => {
    if (sourceId === beforeSourceId || !previous.includes(sourceId)) return previous
    const rest = previous.filter(id => id !== sourceId)
    if (!beforeSourceId) return [...rest, sourceId]
    const index = rest.indexOf(beforeSourceId)
    return index < 0 ? [...rest, sourceId] : [...rest.slice(0, index), sourceId, ...rest.slice(index)]
  })
  const submitHint = !canWrite ? '当前账号没有送剪权限'
    : entries.some(entry => awaitingAutoVariantIds.has(entry.variant.id)) ? '所选原片正在等待自动送剪，请查看任务进度'
    : multiTaskError ? '剪辑任务状态读取失败，请刷新后再试'
      : brandConflict ? '所选原片分属不同品牌，不能合成同一条片子'
        : !selection.length ? '先选择至少一条原片'
          : entries.length !== selection.length ? '部分原片已不可送剪，请先移除'
            : pendingReads ? '正在核对所选原片状态…'
              : blocked.length ? '有原片正在剪辑，请等待结束或移除后再送剪'
                : undefined
  const canSubmit = enabled && canWrite && !submitting && !brandConflict && !multiTaskError
    && !entries.some(entry => awaitingAutoVariantIds.has(entry.variant.id))
    && selection.length >= 1
    && selection.length <= MAX_CUTFLOW_SOURCES
    && selection.every(id => validIds.has(id))
    && entries.length === selection.length
    && !pendingReads

  // Single-source shortcut uses the SAME paid CUT contract as the basket.
  // Revalidate remote state first; do not trust stale status or disrupt cross-task selection.
  const submitOne = async (sourceId: string) => {
    if (!enabled || !canWrite || submitting || quickSubmitGuard.current || selection.length) return
    const source = sources.find(item => item.variant.id === sourceId)
    if (!source || !source.artifactId) return
    quickSubmitGuard.current = true
    setSubmitting(true)
    setMessage('')
    try {
      const [freshState, freshTasks] = await Promise.all([
        listCutflowHandoffs(sourceId),
        listMultiCutflowTasks(),
      ])
      setStates(previous => ({ ...previous, [sourceId]: freshState }))
      setMultiTasks(freshTasks.tasks)
      setMultiTasksLoaded(true)
      setMultiTaskError('')
      if (awaitingAutoVariantIds.has(sourceId) && !freshState.handoffs.length
        && !freshTasks.tasks.some(task => task.sources.some(item => item.variantId === sourceId))) {
        setMessage('这条原片正在等待自动送剪，请查看任务进度。')
        return
      }
      if (!cutflowSourceCanJoinBasket(freshState, source.artifactId)
        || freshTasks.tasks.some(task => multiCutflowPresentation(task).active
          && task.sources.some(item => item.variantId === sourceId))) {
        setMessage('这条原片已有进行中的剪辑，请查看剪辑记录。')
        return
      }
      const previous = freshState.handoffs.find(item => item.sourceArtifactId === source.artifactId)
      const existingVersion = freshTasks.tasks.some(task => task.sources.some(item => item.variantId === sourceId))
      if (previous?.status === 'FAILED') {
        await retryCutflowHandoff(previous.id)
        setMessage('已重新提交剪辑，历史记录保持不变。')
      } else {
        await createMultiCutflowTask([sourceId],
          `${previous || existingVersion ? 'cutflow-revision' : 'cutflow-job'}-${createAttemptKey()}`,
          true, true)
        setMessage(previous || existingVersion ? '已创建新剪辑版本，旧成片仍保留。' : '已送入剪辑。')
      }
      if (onSubmitted) void onSubmitted()
      setRefreshToken(value => value + 1)
    } catch (cause) {
      setMessage(cause instanceof Error && cause.message ? cause.message : '送剪失败，请稍后重试。')
    } finally {
      quickSubmitGuard.current = false
      setSubmitting(false)
    }
  }
  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setMessage('')
    try {
      if (selection.length === 1) {
        // 单条也使用统一合同；旧单源记录仅保留读取和失败重试兼容。
        const only = known[0]
        const current = states[only.variant.id]?.handoffs.find(
          handoff => handoff.sourceArtifactId === only.artifactId,
        )
        const hasPreviousCut = Boolean(current || only.existingMultiTask)
        if (current?.status === 'FAILED') {
          await retryCutflowHandoff(current.id)
          setMessage('已重新提交剪辑，旧记录和结果会继续保留。')
        } else {
          await createMultiCutflowTask(
            selection,
            `${hasPreviousCut ? 'cutflow-revision' : 'cutflow-job'}-${createAttemptKey()}`,
            true,
            true,
          )
          setMessage(hasPreviousCut
            ? '已创建新的剪辑版本，旧成片仍保留。'
            : '已送交剪辑，成片会回到「任务」等待审核。')
        }
      } else {
        // 每次尝试一个新键：请求在飞时按钮已禁用，失败后重试不会复活一个混合篮子。
        const submittedCount = selection.length
        await createMultiCutflowTask(selection, `cutflow-multi-${createAttemptKey()}`, true)
        setMessage(`已送交 ${submittedCount} 条原片一起剪辑，可在「任务」里跟进剪辑进度。`)
      }
      setSelection([])
      if (onSubmitted) void onSubmitted()
      setRefreshToken(value => value + 1)
    } catch (cause) {
      setMessage(cause instanceof Error && cause.message ? cause.message : '送剪失败，请稍后重试。')
    } finally {
      setSubmitting(false)
    }
  }

  return {
    enabled,
    multiTasks,
    multiTasksLoaded,
    multiTaskError,
    multiTasksCheckedAt,
    refreshMultiTasks: () => setRefreshToken(value => value + 1),
    selection,
    entries,
    atCapacity: selection.length >= MAX_CUTFLOW_SOURCES,
    maxSources: MAX_CUTFLOW_SOURCES,
    blocked,
    brandConflict,
    unverifiedBrands,
    canSubmit,
    submitHint,
    submitting,
    message,
    reading,
    pendingReads,
    add,
    remove,
    toggle,
    place,
    move,
    clear: () => { setSelection([]); setMessage('') },
    isSelected: (sourceId: string) => selection.includes(sourceId),
    isInProgress: (sourceId: string) => (
      activeMultiTaskByVariant.has(sourceId)
      || activeSingleStatuses.has(known.find(entry => entry.variant.id === sourceId)?.current?.status ?? '')
    ),
    isPreviouslyCut: (sourceId: string) => (
      multiTaskByVariant.has(sourceId)
      || Boolean(known.find(entry => entry.variant.id === sourceId)?.current)
    ),
    singleSourceVersionsFor: (sourceId: string) => multiTasks.filter(task => (
      task.sources.length === 1 && task.sources.some(source => source.variantId === sourceId)
    )),
    multiSourceTasksForTask: (parentTaskId: string) => multiTasks.filter(task => (
      task.sources.length > 1 && task.sources.every(source => source.taskId === parentTaskId)
    )),
    // Cross-task composites must remain visible from every contributing task.
    relatedMultiSourceTasksForTask: (parentTaskId: string) => multiTasks.filter(task => (
      task.sources.length > 1 && task.sources.some(source => source.taskId === parentTaskId)
    )),
    isBundled: (sourceId: string) => multiTaskByVariant.has(sourceId),
    submit,
    submitOne,
  }
}

export type MultiCutBasketController = ReturnType<typeof useMultiCutBasket>
