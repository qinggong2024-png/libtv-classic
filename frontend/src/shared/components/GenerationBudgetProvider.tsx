import { useEffect, useState, type ReactNode } from 'react'
import { getGenerationBudget } from '../../api'
import { GenerationBudgetContext } from '../../generationBudget'
import type { GenerationBudget } from '../../types'

export function GenerationBudgetProvider({ children }: { children: ReactNode }) {
  const [budget, setBudget] = useState<GenerationBudget | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    let pending = false
    let repeat = false
    let timer: number | undefined
    const refresh = async () => {
      if (document.hidden || controller.signal.aborted) return
      if (pending) { repeat = true; return }
      pending = true
      try {
        const next = await getGenerationBudget(controller.signal)
        if (!controller.signal.aborted) { setBudget(next); setFailed(false) }
      } catch {
        if (!controller.signal.aborted) setFailed(true)
      } finally {
        pending = false
        if (repeat && !controller.signal.aborted) { repeat = false; schedule() }
      }
    }
    const schedule = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => void refresh(), 300)
    }
    void refresh()
    const interval = window.setInterval(() => void refresh(), 15_000)
    window.addEventListener('generation-budget-refresh', schedule)
    window.addEventListener('focus', schedule)
    document.addEventListener('visibilitychange', schedule)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
      window.clearInterval(interval)
      window.removeEventListener('generation-budget-refresh', schedule)
      window.removeEventListener('focus', schedule)
      document.removeEventListener('visibilitychange', schedule)
    }
  }, [])
  return <GenerationBudgetContext.Provider value={{ budget, failed }}>{children}</GenerationBudgetContext.Provider>
}
