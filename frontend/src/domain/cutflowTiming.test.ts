import { describe, expect, it } from 'vitest'
import { cutflowElapsedSeconds } from './cutflowTiming'

const start = '2026-11-19T01:00:00Z'
const later = '2026-11-19T01:02:00Z'
const output = { id: 'edited-1', createdAt: later }
const source = { createdAt: start, updatedAt: later, outputs: [output] }

describe('CUT timing is based on verified handoff/result timestamps', () => {
  it('includes handoff-to-output time exactly once and freezes completed work', () => {
    expect(cutflowElapsedSeconds(source, 'COMPLETED', Date.parse(later) + 3600000)).toBe(120)
  })
  it('counts active time only while the handoff is active', () => {
    expect(cutflowElapsedSeconds({ ...source, outputs: [] }, 'ACTIVE', Date.parse(later))).toBe(120)
  })
  it('does not invent durations for incomplete records or invalid timestamps', () => {
    expect(cutflowElapsedSeconds({ ...source, outputs: [] }, 'COMPLETED', Date.parse(later))).toBeNull()
    expect(cutflowElapsedSeconds({ ...source, createdAt: 'invalid' }, 'COMPLETED', Date.parse(later))).toBeNull()
    expect(cutflowElapsedSeconds(source, 'UNKNOWN', Date.parse(later))).toBeNull()
  })
  it('stops a failed or canceled handoff at its last status update', () => {
    expect(cutflowElapsedSeconds(source, 'FAILED', Date.parse(later) + 60000)).toBe(120)
    expect(cutflowElapsedSeconds(source, 'CANCELLED', Date.parse(later) + 60000)).toBe(120)
  })
  it('never produces negative durations for timestamps reversed in the backend', () => {
    expect(cutflowElapsedSeconds({ ...source, createdAt: later, updatedAt: start }, 'FAILED', Date.parse(later))).toBeNull()
  })
})
