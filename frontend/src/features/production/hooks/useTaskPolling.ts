import { errorMessage } from '../../../errorPresentation'
// 本文件由 App.tsx 拆分生成（2026-09-21 前端架构拆分 v1）。
// 逐字搬运 App() 中与「任务数据获取」相关的状态、refresh/refreshTasks 与两级轮询 effect。
// 轮询节奏保持原样：无活动任务或打开了详情时 30s；有活动任务时 10s；详情页另有 4s 快轮询。
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, getCurrentUser, getSystemStatus, getTask, getTaskPage, setApiUserId, setCsrfCookieName } from '../../../api'
import { ACTIVE_PHASES } from '../../../domain/workflowStates'
import { readStorage } from '../../../safeStorage'
import { canReadTasks } from '../../../pageAccess'
import type { AuthUser, SystemStatus, Task } from '../../../types'

export function useTaskPolling(detailId: string | null) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [system, setSystem] = useState<SystemStatus | null>(null)
  const [actor, setActor] = useState<AuthUser | null>(null)
  const [devActorRole, setDevActorRole] = useState<'user' | 'admin'>(() => readStorage('local', 'development-actor-role') === 'admin' ? 'admin' : 'user')
  const [loading, setLoading] = useState(true)
  const [authRequired, setAuthRequired] = useState(false)
  const [error, setError] = useState('')
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null)
  const [nextTaskCursor, updateNextTaskCursor] = useState<string | null>(null)
  const historyLoaded = useRef(false)
  const taskOwner = useRef<string | null>(null)
  const listRefreshInFlight = useRef<Promise<void> | null>(null)
  const lastListRefreshFinishedAt = useRef(0)
  const setNextTaskCursor = useCallback((cursor: string | null) => {
    // Only history loading advances this cursor, including null at exhaustion.
    historyLoaded.current = true
    updateNextTaskCursor(cursor)
  }, [])
  const [loadingMoreTasks, setLoadingMoreTasks] = useState(false)

  const refreshTasks = useCallback((): Promise<void> => {
    // Manual refresh and the background poll share the same in-flight request.
    if (listRefreshInFlight.current) return listRefreshInFlight.current
    const request = (async () => {
      try {
        const currentActor = await getCurrentUser()
        const page = canReadTasks(currentActor) ? await getTaskPage() : { items: [], nextCursor: null }
        const sameOwner = taskOwner.current === currentActor.userId
        if (!sameOwner || !canReadTasks(currentActor)) historyLoaded.current = false
        taskOwner.current = currentActor.userId
        setActor(currentActor)
        setTasks((current) => {
          const recentIds = new Set(page.items.map((task) => task.id))
          return canReadTasks(currentActor) ? [...page.items, ...(sameOwner ? current.filter((task) => !recentIds.has(task.id)) : [])] : []
        })
        if (!historyLoaded.current) updateNextTaskCursor(page.nextCursor)
        setLastSyncedAt(Date.now())
        setAuthRequired(false)
        setError('')
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 401) {
          setActor(null)
          setTasks([])
          historyLoaded.current = false
          taskOwner.current = null
          updateNextTaskCursor(null)
          setAuthRequired(true)
          setError('')
        } else {
          setError(errorMessage(cause, '暂时连不上网站，请检查网络后刷新页面'))
        }
      } finally {
        lastListRefreshFinishedAt.current = Date.now()
      }
    })()
    listRefreshInFlight.current = request
    void request.then(() => {
      if (listRefreshInFlight.current === request) listRefreshInFlight.current = null
    })
    return request
  }, [])

  const refresh = useCallback(async () => {
    try {
      const status = await getSystemStatus()
      setSystem(status)
      setCsrfCookieName(status.csrfCookieName)
      if (!status.sharedAuthentication) {
        const developmentActorId = devActorRole === 'admin' ? status.phaseOneAdminId : status.phaseOneUserId
        setApiUserId(developmentActorId)
      } else {
        setApiUserId(null)
      }
      const currentActor = await getCurrentUser()
      setActor(currentActor)
      const page = canReadTasks(currentActor) ? await getTaskPage() : { items: [], nextCursor: null }
      taskOwner.current = currentActor.userId
      historyLoaded.current = false
      setTasks(page.items)
      updateNextTaskCursor(page.nextCursor)
      setLastSyncedAt(Date.now())
      setAuthRequired(false)
      setError('')
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        setActor(null)
        setTasks([])
        historyLoaded.current = false
        taskOwner.current = null
        updateNextTaskCursor(null)
        setAuthRequired(true)
        setError('')
      } else {
        setError(errorMessage(cause, '暂时连不上网站，请检查网络后刷新页面'))
      }
    } finally { setLoading(false) }
  }, [devActorRole])

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(initial)
  }, [refresh])

  useEffect(() => {
    if (!error || authRequired) return
    const retry = window.setInterval(() => void refresh(), 5000)
    return () => window.clearInterval(retry)
  }, [authRequired, error, refresh])

  // Detail updates replace task/actor objects; only a cadence or owner change
  // should restart the list clock, otherwise its 30s fallback can starve forever.
  const actorId = actor?.userId
  const hasActiveTasks = tasks.some((task) => ACTIVE_PHASES.has(task.phase))
  const listInterval = detailId || !hasActiveTasks ? 30000 : 10000
  const selected = tasks.find((task) => task.id === detailId)
  const pollDetail = Boolean(actorId && selected && ACTIVE_PHASES.has(selected.phase))

  useEffect(() => {
    if (!actorId) return
    let active = true
    let timer: number | undefined
    let inFlight = false
    const interval = listInterval
    const poll = async () => {
      if (!navigator.onLine || document.visibilityState === 'hidden') {
        if (active) timer = window.setTimeout(() => void poll(), interval)
        return
      }
      if (!active || inFlight) {
        if (active) timer = window.setTimeout(() => void poll(), interval)
        return
      }
      // A recent manual refresh already fetched the latest list. Start the
      // next automatic interval from that completion instead of querying twice.
      const sinceLastRefresh = Date.now() - lastListRefreshFinishedAt.current
      if (lastListRefreshFinishedAt.current && sinceLastRefresh < interval) {
        timer = window.setTimeout(() => void poll(), interval - sinceLastRefresh)
        return
      }
      inFlight = true
      try {
        await refreshTasks()
      } finally {
        inFlight = false
        if (active) timer = window.setTimeout(() => void poll(), interval)
      }
    }
    timer = window.setTimeout(() => void poll(), interval)
    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [actorId, listInterval, refreshTasks])

  useEffect(() => {
    if (!detailId || !pollDetail) return
    let active = true
    let timer: number | undefined
    const poll = async () => {
      if (!active) return
      if (!navigator.onLine || document.visibilityState === 'hidden') {
        timer = window.setTimeout(() => void poll(), 4000)
        return
      }
      try {
        const changed = await getTask(detailId)
        if (active) setTasks((current) => current.map((task) => task.id === changed.id ? changed : task))
      } catch {
        // 更慢的任务列表刷新仍是兜底。
      } finally {
        if (active) timer = window.setTimeout(() => void poll(), 4000)
      }
    }
    timer = window.setTimeout(() => void poll(), 4000)
    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [actorId, detailId, pollDetail])

  return {
    tasks, setTasks,
    system, actor, setActor,
    devActorRole, setDevActorRole,
    loading, setLoading,
    authRequired, setAuthRequired,
    error, setError,
    lastSyncedAt,
    nextTaskCursor, setNextTaskCursor,
    loadingMoreTasks, setLoadingMoreTasks,
    refreshTasks, refresh,
  }
}
