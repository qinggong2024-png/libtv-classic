import { describe, expect, it } from 'vitest'
import { timeBeforeStage, timeBreakdown } from './taskTimeBreakdown'

const at = (seconds: number) => new Date(Date.parse('2026-11-20T00:00:00Z') + seconds * 1000).toISOString()

describe('time breakdown from observed stage windows', () => {
  it('counts actual elapsed once and separates gaps from overlapping stages', () => {
    expect(timeBreakdown(at(0), at(100), [
      { startedAt: at(10), endedAt: at(40) },
      { startedAt: at(25), endedAt: at(60) },
      { startedAt: at(80), endedAt: at(100) },
    ], Date.parse(at(100)))).toEqual({
      totalSeconds: 100, processingSeconds: 70, gapSeconds: 30,
    })
  })

  it('does not double-count two generated videos running at the same time', () => {
    const summary = timeBreakdown(at(0), at(80), [
      { startedAt: at(10), endedAt: at(60) },
      { startedAt: at(10), endedAt: at(60) },
    ], Date.parse(at(80)))
    expect(summary).toEqual({ totalSeconds: 80, processingSeconds: 50, gapSeconds: 30 })
  })

  it('respects active periods and clamps clock at the displayed end', () => {
    expect(timeBreakdown(at(0), at(100), [
      { startedAt: at(20), active: true },
    ], Date.parse(at(120)))).toEqual({
      totalSeconds: 100, processingSeconds: 80, gapSeconds: 20,
    })
  })

  it('does not fabricate negative time or unbounded waiting', () => {
    expect(timeBreakdown(at(100), at(0), [], Date.parse(at(100)))).toBeNull()
    expect(timeBeforeStage(at(0), at(90), at(80))).toBe(0)
    expect(timeBeforeStage(at(0), null, at(15))).toBe(15)
    expect(timeBeforeStage(at(0), null, null)).toBeNull()
  })
})
