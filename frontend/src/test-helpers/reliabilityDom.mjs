import { JSDOM } from 'jsdom'
import { populateGlobal } from 'vitest/runtime'

// Test-only DOM installed from package-lock, shared by the lifecycle suites.
const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://classic.invalid/', pretendToBeVisual: true })
const { keys, originals } = populateGlobal(globalThis, dom.window, { bindFunctions: true })
export function teardownReliabilityDom() {
  dom.window.close()
  const target = globalThis
  keys.forEach((key) => { delete target[key] })
  originals.forEach((value, key) => { target[key] = value })
}
