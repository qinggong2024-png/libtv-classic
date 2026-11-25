import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import type { MultiCutflowTask } from '../api'
import { AUTO_CUTFLOW_WAIT_MS, autoCutflowCandidate, autoCutflowPollDelay, autoCutflowWaitDelayed, partialAutoCutflowKey } from './autoCutflowWaiting'
import { taskCenterState } from './taskCenterState'

const readyAt = Date.parse('2026-11-13T06:23:36Z')
const task = {
  id: 'task-1', phase: 'DELIVERABLE', createdAt: '2026-11-13T00:00:00Z',
  updatedAt: '2026-11-13T06:23:36Z', settings: { autoSubmitToCutflow: true },
  variants: [{ id: 'v1', downloadArtifactId: 'a1', generationStatus: 'SUCCEEDED', downloadStatus: 'READY', validationStatus: 'PASSED' }],
} as unknown as Task
const candidates = [autoCutflowCandidate(task)!]
const batch = (status: string) => ({ id: 'cut-1', status, sources: [{ taskId: task.id }], outputs: [], updatedAt: task.updatedAt } as unknown as MultiCutflowTask)

describe('automatic CUT admission waiting', () => {
  it('uses a shared idempotency key for a terminal one-of-two partial recovery', () => {
    const failure = {
      id: 'v2', downloadArtifactId: null, generationStatus: 'FAILED',
      downloadStatus: 'NOT_READY', validationStatus: 'FAILED',
    }
    const partial = { ...task, phase: 'PARTIALLY_DELIVERABLE', variants: [...task.variants, failure] } as Task
    expect(partialAutoCutflowKey(partial, ['v1'])).toBe('auto-unified-v2:task-1')
    expect(partialAutoCutflowKey(partial, ['v2'])).toBeNull()
    expect(partialAutoCutflowKey(partial, ['v1', 'v2'])).toBeNull()
    expect(partialAutoCutflowKey({ ...partial, variants: [
      task.variants[0], { ...failure, generationStatus: 'UNKNOWN' },
    ] } as Task, ['v1'])).toBeNull()
    expect(partialAutoCutflowKey({ ...partial, settings: { autoSubmitToCutflow: false } } as Task, ['v1'])).toBeNull()
    expect(partialAutoCutflowKey(task, ['v1'])).toBeNull()
  })
  it('starts the timeout at persisted readiness, not task creation or page opening', () => {
    expect(autoCutflowWaitDelayed(task, readyAt + 179_999)).toBe(false)
    expect(autoCutflowWaitDelayed(task, readyAt + AUTO_CUTFLOW_WAIT_MS)).toBe(true)
    expect(autoCutflowWaitDelayed({ ...task }, readyAt + 240_000)).toBe(true)
  })
  it('handles UTC timestamps without an explicit timezone and clock skew', () => {
    expect(autoCutflowCandidate({ ...task, updatedAt: '2026-11-13T06:23:36' })?.readyAt).toBe(readyAt)
    expect(autoCutflowWaitDelayed(task, readyAt - 10_000)).toBe(false)
    expect(autoCutflowWaitDelayed({ ...task, updatedAt: 'invalid' }, readyAt + 999_999)).toBe(false)
  })
  it('requires every original to be generated, validated and downloaded', () => {
    for (const change of [
      { generationStatus: 'FAILED' }, { generationStatus: 'UNKNOWN' },
      { downloadStatus: 'PENDING' }, { validationStatus: 'FAILED' }, { downloadArtifactId: null },
    ]) expect(autoCutflowCandidate({ ...task, variants: [task.variants[0], { ...task.variants[0], ...change }] })).toBeNull()
    expect(autoCutflowCandidate({ ...task, variants: [] })).toBeNull()
    expect(autoCutflowCandidate({ ...task, phase: 'CANCELLED' })).toBeNull()
    expect(autoCutflowCandidate({ ...task, settings: { autoSubmitToCutflow: false } })).toBeNull()
    expect(autoCutflowCandidate({ ...task, settings: {} })).toBeNull()
  })
  it('polls at five seconds only in the initial bounded admission window', () => {
    expect(autoCutflowPollDelay(candidates, [], readyAt, false)).toBe(5_000)
    expect(autoCutflowPollDelay(candidates, [], readyAt + 179_999, true)).toBe(5_000)
    expect(autoCutflowPollDelay(candidates, [], readyAt + 180_000, false)).toBe(30_000)
    expect(autoCutflowPollDelay([{ taskId: 't', readyAt: null }], [], readyAt, false)).toBe(30_000)
  })
  it('uses normal intervals for accepted jobs and backs off after read failures', () => {
    expect(autoCutflowPollDelay(candidates, [batch('IMPORTING')], readyAt, true)).toBe(15_000)
    expect(autoCutflowPollDelay(candidates, [batch('FAILED')], readyAt, false)).toBe(30_000)
    expect(autoCutflowPollDelay(candidates, [], readyAt, false, true)).toBe(30_000)
  })
  it('keeps waiting in progress and moves only overdue waits to attention', () => {
    expect(taskCenterState(task, [], { now: readyAt + 179_999 })).toMatchObject({ group: 'active', label: '等待自动送剪', manualSelection: false })
    expect(taskCenterState(task, [], { now: readyAt + 180_000 })).toMatchObject({ group: 'attention', label: '自动送剪等待较久', manualSelection: false })
  })
  it('never replaces an accepted job or a read failure with a timeout diagnosis', () => {
    expect(taskCenterState(task, [batch('ACCEPTED')], { now: readyAt + 900_000 })).toMatchObject({ group: 'active', label: 'CUT 已接单，排队中' })
    expect(taskCenterState(task, [batch('IMPORTING')], { now: readyAt + 900_000 })).toMatchObject({ group: 'active', label: 'CUT 已接单，正在导入' })
    expect(taskCenterState(task, [], { error: true, now: readyAt + 900_000 }).label).toBe('剪辑状态同步异常')
    expect(taskCenterState(task, [], { loaded: false, now: readyAt + 900_000 }).label).toBe('正在核对剪辑状态')
  })
})
