import { describe, expect, it } from 'vitest'
import type { Task } from '../../types'
import type { CutflowHandoff } from '../../api'
import { cutflowSources, cutflowSourceSummary, cutflowBrandConflict, cutflowUnverifiedBrandCount, cutflowSourceCanJoinBasket, moveCutflowSource, reconcileCutflowSelection, toggleCutflowSelection, MAX_CUTFLOW_SOURCES, type CutflowSourceState } from './cutflowLibraryModel'

const tasks = [{ id: 'task', title: 'shirt', updatedAt: '2026-10-18', phase: 'PARTIALLY_DELIVERABLE', variants: [
  { id: 'stable', downloadArtifactId: 'original' }, { id: 'dynamic', downloadArtifactId: null },
] }] as Task[]
const source = cutflowSources(tasks)[0]
const handoff = (artifact = 'original', outputs: CutflowHandoff['outputs'] = []) => ({
  id: artifact, sourceArtifactId: artifact, status: 'PENDING_REVIEW', outputs,
}) as CutflowHandoff
const output = { id: 'render' } as CutflowHandoff['outputs'][number]

describe('editing library source ownership and classification', () => {
  it('preserves user order, supports keyboard-style movement, and reconciles refreshes without dropping valid picks', () => {
    expect(moveCutflowSource(['a', 'b', 'c'], 'c', -1)).toEqual(['a', 'c', 'b'])
    expect(moveCutflowSource(['a', 'b'], 'a', -1)).toEqual(['a', 'b'])
    expect(reconcileCutflowSelection(['a', 'stale', 'b'], new Set(['a', 'b']))).toEqual(['a', 'b'])
    expect(toggleCutflowSelection(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggleCutflowSelection(['a', 'b'], 'a')).toEqual(['b'])
    const full = Array.from({ length: MAX_CUTFLOW_SOURCES }, (_, index) => `source-${index}`)
    expect(toggleCutflowSelection(full, 'source-9')).toEqual(full)
  })
  it('warns on missing brand identity and blocks only confirmed cross-brand selections', () => {
    const sameBrand = [{ ...source, task: { ...source.task, siteProfileId: 'guidingcross' } }]
    expect(cutflowBrandConflict(sameBrand)).toBe(false)
    const missingBrand = [{ ...source, task: { ...source.task, siteProfileId: null } }]
    expect(cutflowBrandConflict(missingBrand)).toBe(false)
    expect(cutflowUnverifiedBrandCount(missingBrand)).toBe(1)
    expect(cutflowBrandConflict([
      { ...source, task: { ...source.task, siteProfileId: 'guidingcross' } },
      { ...source, task: { ...source.task, siteProfileId: 'other-brand' } },
    ])).toBe(true)
  })
  it('includes downloadable variants in partial tasks and excludes unavailable variants', () => {
    expect(cutflowSources(tasks).map(item => item.variant.id)).toEqual(['stable'])
  })
  it('does not label unknown, disabled, or failed reads as unsent', () => {
    for (const state of [undefined, { enabled: false, handoffs: [] }, { enabled: true, handoffs: [], error: 'offline' }]) {
      expect(cutflowSourceSummary(source, state).filter).toBeNull()
    }
  })
  it('classifies unsent, awaiting return, and returned outputs separately', () => {
    const state: CutflowSourceState = { enabled: true, handoffs: [] }
    expect(cutflowSourceSummary(source, state).filter).toBe('cutflow-ready')
    state.handoffs = [handoff()]
    expect(cutflowSourceSummary(source, state).filter).toBe('cutflow-editing')
    state.handoffs = [handoff('original', [output])]
    expect(cutflowSourceSummary(source, state).filter).toBe('cutflow-finished')
  })
  it('keeps failed and canceled handoffs out of unsent even without outputs', () => {
    for (const status of ['FAILED', 'CANCELED']) {
      expect(cutflowSourceSummary(source, { enabled: true, handoffs: [{ ...handoff(), status }] }).filter).toBe('cutflow-editing')
    }
  })
  it('allows failed, canceled, or returned sources to join a revision basket and blocks active edits', () => {
    for (const status of ['FAILED', 'CANCELED']) {
      expect(cutflowSourceCanJoinBasket(
        { enabled: true, handoffs: [{ ...handoff(), status }] },
        'original',
      )).toBe(true)
    }
    expect(cutflowSourceCanJoinBasket(
      { enabled: true, handoffs: [handoff('original', [output])] },
      'original',
    )).toBe(true)
    for (const status of ['QUEUED', 'EDITING']) {
      expect(cutflowSourceCanJoinBasket(
        {
          enabled: true,
          handoffs: [{ ...handoff(), status }],
        },
        'original',
      )).toBe(false)
    }
    expect(cutflowSourceCanJoinBasket({ enabled: true, handoffs: [] }, 'original')).toBe(true)
    expect(cutflowSourceCanJoinBasket({ enabled: false, handoffs: [] }, 'original')).toBe(false)
    expect(cutflowSourceCanJoinBasket({ enabled: true, handoffs: [], error: 'offline' }, 'original')).toBe(false)
  })
  it('never attaches outputs belonging to a previous artifact', () => {
    const summary = cutflowSourceSummary(source, { enabled: true, handoffs: [handoff('old', [output]), handoff()] })
    expect(summary.filter).toBe('cutflow-editing')
    expect(summary.outputs).toEqual([])
    expect(summary.current?.sourceArtifactId).toBe('original')
  })
  it('retains known outputs during a failed refresh', () => {
    expect(cutflowSourceSummary(source, { enabled: true, handoffs: [handoff('original', [output])], error: 'offline' }).filter).toBe('cutflow-finished')
  })
})
