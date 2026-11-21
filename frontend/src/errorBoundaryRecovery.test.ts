import { describe, expect, it } from 'vitest'
import { currentFrontendBuild, isModuleLoadError } from './errorBoundaryRecovery'

describe('module-load error recovery', () => {
  it('recognizes stale JS and CSS chunk failures', () => {
    expect(isModuleLoadError(new TypeError('Failed to fetch dynamically imported module: /assets/HomeView-old.js'))).toBe(true)
    expect(isModuleLoadError(new Error('Loading chunk 12 failed'))).toBe(true)
    expect(isModuleLoadError(new Error('Importing a module script failed'))).toBe(true)
  })

  it('does not reload for ordinary render or API errors', () => {
    expect(isModuleLoadError(new Error('Cannot read properties of undefined'))).toBe(false)
    expect(isModuleLoadError(new Error('API request failed: 502'))).toBe(false)
    expect(isModuleLoadError(null)).toBe(false)
  })

  it('reports a safe build marker without reading private data', () => {
    expect(currentFrontendBuild()).toMatch(/^(unknown|index-[a-zA-Z0-9_-]+\.js)$/)
  })
})
