import { act, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
export async function mount(element: ReactNode) {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  await act(async () => { root.render(element) })
  return {
    host,
    async render(next: ReactNode) { await act(async () => { root.render(next) }) },
    async unmount() { await act(async () => { root.unmount() }); host.remove() },
  }
}
