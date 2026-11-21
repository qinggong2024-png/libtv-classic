import { useEffect, useState } from 'react'
import { listImageGenerationBatches } from './api'
import type { ImageGenerationBatch } from './types'

export function useImageHistory(scope: string | null, enabled = true, onPage?: (batches: ImageGenerationBatch[]) => void) {
  const [history, setHistory] = useState({ scope: null as string | null, batches: [] as ImageGenerationBatch[], loading: false, error: '', hasMore: false, nextOffset: 0 })
  const [query, setQuery] = useState({ scope, offset: 0, revision: 0 })
  useEffect(() => {
    if (!scope || !enabled) return
    const controller = new AbortController()
    const offset = query.scope === scope ? query.offset : 0
    void listImageGenerationBatches(offset, controller.signal).then((page) => {
      if (controller.signal.aborted) return
      setHistory((current) => ({ scope, batches: offset === 0 || current.scope !== scope ? page.items : [...current.batches, ...page.items], loading: false, error: '', hasMore: page.hasMore, nextOffset: page.nextOffset }))
      onPage?.(page.items)
    }).catch(() => {
      if (!controller.signal.aborted) setHistory((current) => ({
        ...current, scope, batches: current.scope === scope ? current.batches : [], loading: false,
        error: '服务端生图历史读取失败，请重试。已有记录仍可查看。',
      }))
    })
    return () => controller.abort()
  }, [scope, enabled, query, onPage])
  function request(offset: number) {
    setHistory((current) => ({ ...current, loading: true, error: '' }))
    setQuery((current) => ({ scope, offset, revision: current.revision + 1 }))
  }
  const scoped = history.scope === scope
  return {
    batches: scoped ? history.batches : [],
    loading: Boolean(scope && enabled && (!scoped || history.loading)),
    error: scoped ? history.error : '',
    hasMore: scoped && history.hasMore,
    refresh: () => request(0),
    loadMore: () => request(scoped ? history.nextOffset : 0),
  }
}
