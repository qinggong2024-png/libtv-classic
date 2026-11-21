import { describe, expect, it } from 'vitest'
import type { MultiCutflowTask } from '../api'
import { hasUnseenCutflowOutput } from './cutflowUnread'

const batch = (id: string, updatedAt: string, viewedAt: string | null | undefined) => ({
  id, updatedAt, outputs: [{ id: id + '-output', viewedAt }],
} as unknown as MultiCutflowTask)

describe('new CUT output indicator', () => {
  const old = batch('old', '2026-11-10T10:00:00Z', '2026-11-10T11:00:00Z')
  const fresh = batch('new', '2026-11-11T12:00:00Z', null)
  it('is hidden until CUT history has actually loaded', () => {
    expect(hasUnseenCutflowOutput([fresh], false)).toBe(false)
    expect(hasUnseenCutflowOutput([fresh], true, true)).toBe(false)
  })
  it('only the latest returned output determines the dot', () => {
    expect(hasUnseenCutflowOutput([old, fresh], true)).toBe(true)
    expect(hasUnseenCutflowOutput([fresh, old], true)).toBe(true)
    expect(hasUnseenCutflowOutput([old], true)).toBe(false)
  })
  it('new version is unread even after the prior version was viewed', () => {
    const viewedFresh = batch('new', '2026-11-11T12:00:00Z', '2026-11-11T12:10:00Z')
    expect(hasUnseenCutflowOutput([old, viewedFresh], true)).toBe(false)
    expect(hasUnseenCutflowOutput([viewedFresh, batch('newer', '2026-11-11T13:00:00Z', null)], true)).toBe(true)
  })
  it('does not invent unread status when an older API omitted viewedAt', () => {
    expect(hasUnseenCutflowOutput([batch('legacy', '2026-10-01T00:00:00Z', undefined)], true)).toBe(false)
  })
})
