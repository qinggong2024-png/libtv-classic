import type { ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

export function TaskDetailDisclosure({ title, hint, children, id, open = false }: {
  title: string
  hint?: string
  children: ReactNode
  id?: string
  open?: boolean
}) {
  return <details className="task-a-disclosure" id={id} open={open}>
    <summary><ChevronRight size={16} aria-hidden="true" /><strong>{title}</strong>
      {hint ? <span className="task-a-disclosure__hint">{hint}</span> : null}
      <span className="task-a-disclosure__toggle" aria-hidden="true"><span className="when-closed">展开</span><span className="when-open">收起</span></span>
    </summary>
    <div className="task-a-disclosure__body">{children}</div>
  </details>
}
