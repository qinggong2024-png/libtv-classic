// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { installPageVersionSync } from './pageVersionSync'

const cleanups: Array<() => void> = []
afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup()
  vi.restoreAllMocks()
})

function currentDocument(bundle = 'old'): Document {
  const page = document.implementation.createHTMLDocument('ALLWE')
  page.head.innerHTML = `<script type="module" src="/assets/index-${bundle}.js"></script>`
  return page
}

function fetchedHtml(bundle = 'new'): string {
  return `<!doctype html><html><head><script type="module" src="/assets/index-${bundle}.js"></script></head><body></body></html>`
}

function showPage(persisted: boolean) {
  const event = new Event('pageshow') as PageTransitionEvent
  Object.defineProperty(event, 'persisted', { value: persisted })
  window.dispatchEvent(event)
}

function createFixture(html: string = fetchedHtml()) {
  const fetchPage = vi.fn(async () => ({ ok: true, text: async () => html }))
  const reload = vi.fn()
  const page = currentDocument()
  cleanups.push(installPageVersionSync({ page, browser: window,
    fetchPage: fetchPage as unknown as typeof fetch, reload }))
  return { fetchPage, reload, page }
}

describe('page version sync', () => {
  it('does not request an extra version check on fresh navigation', async () => {
    const { fetchPage, reload } = createFixture()
    showPage(false)
    await vi.waitFor(() => expect(fetchPage).not.toHaveBeenCalled())
    expect(reload).not.toHaveBeenCalled()
  })

  it('updates only an untouched page restored from bfcache', async () => {
    const { fetchPage, reload } = createFixture()
    showPage(true)
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1))
    expect(fetchPage).toHaveBeenCalledWith('/', { cache: 'no-store', credentials: 'same-origin' })
  })

  it('does not refresh when the published bundle is unchanged', async () => {
    const { fetchPage, reload } = createFixture(fetchedHtml('old'))
    showPage(true)
    await vi.waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1))
    expect(reload).not.toHaveBeenCalled()
  })

  it('keeps edited, clicked and uploading pages running', async () => {
    for (const interaction of ['input', 'click', 'change', 'paste', 'drop', 'submit']) {
      const { fetchPage, reload, page } = createFixture()
      page.dispatchEvent(new Event(interaction, { bubbles: true }))
      showPage(true)
      expect(fetchPage).not.toHaveBeenCalled()
      expect(reload).not.toHaveBeenCalled()
      cleanups.pop()?.()
    }
  })

  it('does not reload if the user interacts while a check is in flight', async () => {
    let finishFetch: (value: { ok: boolean; text: () => Promise<string> }) => void = () => {}
    const fetchPage = vi.fn(() => new Promise<{ ok: boolean; text: () => Promise<string> }>(resolve => { finishFetch = resolve }))
    const reload = vi.fn()
    const page = currentDocument()
    cleanups.push(installPageVersionSync({ page, browser: window,
      fetchPage: fetchPage as unknown as typeof fetch, reload }))
    showPage(true)
    page.dispatchEvent(new Event('input', { bubbles: true }))
    finishFetch({ ok: true, text: async () => fetchedHtml() })
    await vi.waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1))
    await Promise.resolve()
    expect(reload).not.toHaveBeenCalled()
  })

  it('ignores an unavailable network or non-HTML response', async () => {
    const fetchPage = vi.fn().mockRejectedValue(new Error('offline'))
    const reload = vi.fn()
    const page = currentDocument()
    cleanups.push(installPageVersionSync({ page, browser: window,
      fetchPage: fetchPage as unknown as typeof fetch, reload }))
    showPage(true)
    await vi.waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1))
    await Promise.resolve()
    expect(reload).not.toHaveBeenCalled()
  })
})
