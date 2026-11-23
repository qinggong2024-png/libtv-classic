import { useCallback, useEffect, useRef, useState } from 'react'
import {
  acknowledgeTaskAttention, clearTaskAttentionAcknowledgement, listTaskAttentionAcknowledgements,
} from '../api'
import type { TaskAttentionMap } from '../domain/taskAttention'

export function useTaskAttentionAcknowledgements(userId: string | null) {
  const [snapshot, setSnapshot] = useState<{ userId: string | null; entries: TaskAttentionMap }>({ userId: null, entries: {} })
  const [errorState, setErrorState] = useState<{ userId: string | null; message: string }>({ userId: null, message: '' })
  const pending = useRef<{ userId: string | null; overrides: Record<string, string | null> }>({ userId: null, overrides: {} })
  const acknowledgements = snapshot.userId === userId ? snapshot.entries : {}

  useEffect(() => {
    if (!userId) return
    let active = true
    pending.current = { userId, overrides: {} }
    void listTaskAttentionAcknowledgements().then(items => {
      if (active) {
        const entries: TaskAttentionMap = Object.fromEntries(items.map(item => [item.taskId, item.signature]))
        if (pending.current.userId === userId) {
          for (const [taskId, value] of Object.entries(pending.current.overrides)) {
            if (value === null) delete entries[taskId]
            else entries[taskId] = value
          }
          pending.current.overrides = {}
        }
        setSnapshot({ userId, entries })
        setErrorState({ userId, message: '' })
      }
    }).catch(() => {
      if (active) setErrorState({ userId, message: '任务提醒状态同步失败，请刷新后重试' })
    })
    return () => { active = false }
  }, [userId])

  const acknowledge = useCallback(async (taskId: string, signature: string) => {
    if (!userId) return
    const previous = snapshot.userId === userId ? snapshot.entries[taskId] : undefined
    if (pending.current.userId === userId) pending.current.overrides[taskId] = signature
    setSnapshot(current => ({ userId, entries: { ...(current.userId === userId ? current.entries : {}), [taskId]: signature } }))
    setErrorState({ userId, message: '' })
    try {
      await acknowledgeTaskAttention(taskId, signature)
    } catch {
      if (pending.current.userId === userId && pending.current.overrides[taskId] === signature) delete pending.current.overrides[taskId]
      setSnapshot(current => {
        if (current.userId !== userId || current.entries[taskId] !== signature) return current
        const entries = { ...current.entries }
        if (previous !== undefined) entries[taskId] = previous
        else delete entries[taskId]
        return { userId, entries }
      })
      setErrorState({ userId, message: '未能保存“不再提醒”，请重试' })
    }
  }, [snapshot, userId])

  const restore = useCallback(async (taskId: string) => {
    if (!userId) return
    const previous = snapshot.userId === userId ? snapshot.entries[taskId] : undefined
    if (pending.current.userId === userId) pending.current.overrides[taskId] = null
    setSnapshot(current => {
      if (current.userId !== userId) return current
      const entries = { ...current.entries }
      delete entries[taskId]
      return { userId, entries }
    })
    setErrorState({ userId, message: '' })
    try {
      await clearTaskAttentionAcknowledgement(taskId)
    } catch {
      if (pending.current.userId === userId && pending.current.overrides[taskId] === null) delete pending.current.overrides[taskId]
      if (previous !== undefined) setSnapshot(current => current.userId === userId
        ? { userId, entries: { ...current.entries, [taskId]: previous } } : current)
      setErrorState({ userId, message: '恢复提醒失败，请重试' })
    }
  }, [snapshot, userId])

  return { acknowledgements, acknowledge, restore, error: errorState.userId === userId ? errorState.message : '' }
}
