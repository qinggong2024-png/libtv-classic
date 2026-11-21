import type { ReactNode } from 'react'
import type { Task, Variant } from '../../types'
import { MultiCutBasket } from './MultiCutBasket'
import { useMultiCutBasket, type MultiCutBasketController } from './useMultiCutBasket'

// Selection and in-flight submissions outlive the route-specific presentation.
export function SharedCutflowWorkspace({ tasks, enabled, canWrite, storageKey, visible, scopeKey, compact = true, onSubmitted, onPlay, children }: {
  tasks: Task[]
  enabled: boolean
  canWrite: boolean
  storageKey: string
  visible: boolean
  scopeKey: string
  compact?: boolean
  onSubmitted: () => void | Promise<void>
  onPlay: (task: Task, variant: Variant) => void
  children: (basket: MultiCutBasketController) => ReactNode
}) {
  const basket = useMultiCutBasket({ tasks, enabled, canWrite, storageKey, onSubmitted })
  return <>
    {children(basket)}
    {enabled && visible ? <MultiCutBasket key={scopeKey} basket={basket} compact={compact} onPlay={(taskId, variantId) => {
      const task = tasks.find(item => item.id === taskId)
      const variant = task?.variants.find(item => item.id === variantId)
      if (task && variant) onPlay(task, variant)
    }} /> : null}
  </>
}
