/** Identify failed code-split module downloads; do not treat ordinary React errors as stale builds. */
export function isModuleLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false
  return /(?:Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Loading (?:CSS )?chunk [^\n]* failed|ChunkLoadError)/i.test(`${error.name}: ${error.message}`)
}

export function currentFrontendBuild(): string {
  if (typeof document === 'undefined') return 'unknown'
  const entry = document.querySelector<HTMLScriptElement>('script[type="module"][src]')
  const filename = entry?.src.split('/').pop() ?? ''
  return /^index-[a-zA-Z0-9_-]+\.js$/.test(filename) ? filename : 'unknown'
}
