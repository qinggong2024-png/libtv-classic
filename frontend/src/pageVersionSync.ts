/**
 * New navigations always receive the latest index.html (Cache-Control: no-store).
 * A browser may instead restore an already loaded document from the back/forward
 * cache. Only then compare the entry bundle with the current server entry.
 *
 * Never poll while the user works, never reload after any interaction, and never
 * use a production API endpoint. An unavailable version check fails silently.
 */
function entrypointPath(documentToRead: Document, baseUrl: string): string | null {
  const script = documentToRead.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/index-"]')
  if (!script) return null
  try {
    const url = new URL(script.getAttribute('src') ?? '', baseUrl)
    return url.pathname.startsWith('/assets/index-') && url.pathname.endsWith('.js') ? url.pathname : null
  } catch {
    return null
  }
}

export function installPageVersionSync({
  page = document,
  browser = window,
  fetchPage = fetch,
  reload = () => window.location.reload(),
}: {
  page?: Document
  browser?: Window
  fetchPage?: typeof fetch
  reload?: () => void
} = {}): () => void {
  let interacted = false
  let checking = false
  const recordInteraction = () => { interacted = true }
  // Includes clicks that initiate a paid action, edits, selections and uploads.
  const interactionEvents = ['click', 'input', 'change', 'paste', 'drop', 'submit'] as const
  for (const event of interactionEvents) page.addEventListener(event, recordInteraction, { capture: true })

  const onPageShow = (event: PageTransitionEvent) => {
    // A new navigation already fetched no-store index.html. Do not add a request.
    if (!event.persisted || interacted || checking) return
    const currentEntry = entrypointPath(page, browser.location.href)
    if (!currentEntry) return
    checking = true
    void (async () => {
      try {
        const response = await fetchPage('/', { cache: 'no-store', credentials: 'same-origin' })
        if (!response.ok || interacted) return
        const html = await response.text()
        if (interacted) return
        const latestPage = new DOMParser().parseFromString(html, 'text/html')
        const nextEntry = entrypointPath(latestPage, browser.location.href)
        if (nextEntry && nextEntry !== currentEntry && !interacted) reload()
      } catch {
        // A transient network failure must not interrupt a usable page.
      } finally {
        checking = false
      }
    })()
  }
  browser.addEventListener('pageshow', onPageShow)

  return () => {
    browser.removeEventListener('pageshow', onPageShow)
    for (const event of interactionEvents) page.removeEventListener(event, recordInteraction, { capture: true })
  }
}
