import { act } from 'react'
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getOperations, setOperationalPause } from './api'
import type { OperationsSnapshot } from './types'
import { OperationsPanel } from './OperationsPanel'
import { mount } from './test-helpers/reactHarness'
import { teardownReliabilityDom } from './test-helpers/reliabilityDom.mjs'

vi.mock('./api', async (original) => ({
  ...await original<typeof import('./api')>(),
  getOperations: vi.fn(),
  setOperationalPause: vi.fn(),
}))
vi.mock('./features/admin/OperationsIssuesBrowser', () => ({
  OperationsIssuesBrowser: ({ category }: { category: string }) => <div data-issue-browser={category}>异常明细</div>,
}))

function makeSnapshot({ unknown = 0, reserved = 0, blocked = 0, alerts = 0, paused = false } = {}): OperationsSnapshot {
  return {
    sampledAt: '2026-11-24T00:00:00Z',
    runtime: {
      version: 7,
      config: {
        globalPaused: paused,
        paidSubmissionsPaused: false,
        generationConcurrency: { sameProject: 1, perUser: 2, global: 5 },
        preparedQueue: { lowWatermark: 3, highWatermark: 8 },
        autoConfirmLevels: [],
      },
    },
    queues: { prepared: 0, generation: 0, running: 2, unknown: 0 },
    productionHealth: {
      queue: {
        actionableTotal: 3,
        actionablePrepared: 3,
        dispatchPending: 0,
        readyCommands: 0,
        blockedBudgetUsd: blocked,
        blockedLegacyLimit: 0,
        blockedOther: 0,
        oldestActionableWaitSeconds: 60,
        launched30m: 2,
      },
      cloudUnknownAttempts: unknown,
      reservationsAwaitingReview: reserved,
      issueSamples: { blockedPrepared: [], cloudUnknown: [], reservationReview: [] },
      reservationOutcomes: { succeeded: 0, failed: 0, cloudUnknown: 0, other: 0 },
    },
    metrics: {
      duplicateGenerationTotal: 0,
      crossTaskConnectionTotal: 0,
      unknownAfterSubmitRate: 0,
      generationSlotUtilization: 0.4,
      runnerActiveCommandHosts: 1,
      runnerOnlineHosts: 2,
      runnerOrphanProcesses: 0,
      pointsReserved: 0,
      pointsConsumed: 0,
      pointsUncertain: 0,
    },
    pipelinePerformance: {
      windowDays: 7,
      stages: {},
      automationBottleneck: null,
      currentConstraint: 'IDLE',
      throughput: { completedVideos24h: 12, completedTasks24h: 6 },
      backlog: { analysis: 0, confirmation: 0, materialization: 0, queued: 0, generation: 0, delivery: 0 },
    },
    runners: [],
    canvases: [],
    points: {},
    alerts: Array.from({ length: alerts }, (_, i) => ({ code: `ALERT_${i}`, severity: 'WARNING', action: 'RECONCILE_BEFORE_RETRY' })),
  }
}
let view: Awaited<ReturnType<typeof mount>> | null = null
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.mocked(getOperations).mockResolvedValue(makeSnapshot())
})
afterEach(async () => {
  if (view) await view.unmount()
  view = null
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})
afterAll(teardownReliabilityDom)

async function showOverview(snapshot: OperationsSnapshot, onShowTasks = vi.fn()) {
  vi.mocked(getOperations).mockResolvedValue(snapshot)
  view = await mount(<OperationsPanel adminId="admin-id" onShowTasks={onShowTasks} onShowBilling={vi.fn()} />)
  await act(async () => { await new Promise((done) => setTimeout(done, 45)) })
  expect(getOperations).toHaveBeenCalledWith('admin-id')
  return { host: view.host, onShowTasks }
}

describe('compact operations overview', () => {
  it('prioritizes actionable anomalies, keeps drill-down and task shortcut in reach', async () => {
    const { host, onShowTasks } = await showOverview(makeSnapshot({ unknown: 2, reserved: 1, blocked: 3 }))
    const issues = host.querySelector('.ops-issues')
    const metrics = host.querySelector('.ops-key-metrics')
    const controls = host.querySelector<HTMLDetailsElement>('.ops-management-details')
    expect(issues).not.toBeNull()
    expect(metrics).not.toBeNull()
    expect(controls).not.toBeNull()
    expect(issues!.compareDocumentPosition(metrics!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(metrics!.compareDocumentPosition(controls!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(controls?.open).toBe(false)
    expect(host.querySelector('.ops-advanced-details')).toBeNull()
    expect(issues?.querySelectorAll('.ops-issue-row')).toHaveLength(3)
    const button = host.querySelector<HTMLButtonElement>('.ops-command-actions .primary-button')
    await act(async () => button?.click())
    expect(onShowTasks).toHaveBeenCalledTimes(1)
    const drilldown = issues!.querySelector<HTMLButtonElement>('.ops-issue-row.is-danger .text-button')
    await act(async () => drilldown?.click())
    expect(host.querySelector('[data-issue-browser="cloudUnknown"]')).not.toBeNull()
  })

  it('hides zero-count anomalies without mistaking a paused scheduler for normal production', async () => {
    const { host } = await showOverview(makeSnapshot({ paused: true }))
    expect(host.querySelectorAll('.ops-issue-row')).toHaveLength(0)
    expect(host.querySelector('.ops-issues')).toBeNull()
    expect(host.textContent).toContain('暂无待处理项')
    expect(host.querySelector('.ops-command-actions .primary-button')).toBeNull()
    expect(host.querySelector('.ops-command-actions .secondary-button:not(.ops-refresh-action)')).toBeNull()
    expect(host.textContent).toContain('生产开关已暂停')
    expect(host.textContent).toContain('受调度暂停影响')
    expect(host.querySelectorAll('.ops-key-metrics > div')).toHaveLength(4)
    expect(setOperationalPause).not.toHaveBeenCalled()
  })

  it('requires explicit confirmation before pausing paid work', async () => {
    const { host } = await showOverview(makeSnapshot())
    const controls = host.querySelector<HTMLDetailsElement>('.ops-management-details')
    await act(async () => { controls?.querySelector('summary')?.click() })
    expect(controls?.open).toBe(true)
    const pauseButton = [...host.querySelectorAll<HTMLButtonElement>('.ops-control-action')]
      .find((button) => button.textContent?.includes('暂停付费确认'))
    await act(async () => pauseButton?.click())
    expect(host.querySelector('[role="alertdialog"]')).not.toBeNull()
    expect(setOperationalPause).not.toHaveBeenCalled()
    const confirmButton = host.querySelector<HTMLButtonElement>('.ops-confirmation .secondary-button--danger, .ops-confirmation .primary-button--danger')
    await act(async () => { confirmButton?.click(); await Promise.resolve() })
    expect(setOperationalPause).toHaveBeenCalledWith('admin-id', 7, 'PAID_SUBMISSIONS', true, expect.any(String))
  })

  it('surfaces safety alerts even when issue counters are zero', async () => {
    const { host } = await showOverview(makeSnapshot({ alerts: 1 }))
    expect(host.querySelector('.ops-safety-alerts')).not.toBeNull()
    expect(host.textContent).toContain('1 类事项待处理')
    expect(host.querySelector('.ops-issues')).toBeNull()
  })
})
