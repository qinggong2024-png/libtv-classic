// 本文件由 App.tsx 拆分生成（2026-09-28 前端架构拆分 v2）。
// 代码为逐字搬运，未经逻辑改写。
import { useEffect, useRef } from 'react'

export function useDismiss(open: boolean, onClose: () => void, containerRef?: React.RefObject<HTMLElement | null>) {
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])
  useEffect(() => {
    if (!open) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusableSelector = 'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), video[controls], audio[controls], [tabindex]'
    const container = containerRef?.current
    const focusable = () => Array.from(container?.querySelectorAll<HTMLElement>(focusableSelector) ?? [])
      .filter((element) => element.tabIndex >= 0 && !element.hasAttribute('data-dismiss-focus-guard')
        && !element.closest('[hidden], [inert]') && element.getClientRects().length > 0)
    // Tab must traverse the browser's native media controls. Wrapping on a
    // keydown targeted at <video> would skip its entire shadow control tree.
    const startGuard = document.createElement('span')
    const endGuard = document.createElement('span')
    for (const guard of [startGuard, endGuard]) {
      guard.tabIndex = 0
      guard.setAttribute('data-dismiss-focus-guard', '')
      guard.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none'
    }
    startGuard.onfocus = () => focusable().at(-1)?.focus()
    endGuard.onfocus = () => focusable()[0]?.focus()
    container?.prepend(startGuard)
    container?.append(endGuard)
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current()
    }
    document.body.classList.add('overlay-open')
    window.addEventListener('keydown', close)
    focusable()[0]?.focus()
    return () => {
      startGuard.remove()
      endGuard.remove()
      document.body.classList.remove('overlay-open')
      window.removeEventListener('keydown', close)
      previousFocus?.focus()
    }
  }, [containerRef, open])
}
