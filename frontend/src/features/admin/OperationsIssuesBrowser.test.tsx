import { act } from 'react'
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getOperationsIssues } from '../../api'
import type { OperationsIssuePage, OperationsIssueRecord } from '../../types'
import { mount } from '../../test-helpers/reactHarness'
import { teardownReliabilityDom } from '../../test-helpers/reliabilityDom.mjs'
import { OperationsIssuesBrowser } from './OperationsIssuesBrowser'

vi.mock('../../api', async (original) => ({
  ...await original<typeof import('../../api')>(),
  getOperationsIssues: vi.fn(),
}))

const record = {
  taskId: 'cross-account-task-id',
  title: '另一个品牌的异常视频',
  username: 'brand-account',
  brandName: 'Pagewings',
  taskPhase: 'ACTION_REQUIRED',
  phaseReasonCode: 'AGGREGATED_ACTION_REQUIRED',
  taskCreatedAt: '2026-11-20T10:00:00Z',
  variantType: 'STABLE',
  attemptId: 'attempt-1',
  attemptStatus: 'UNKNOWN_AFTER_SUBMIT',
  provider: 'kling',
  providerTaskId: 'cloud-123',
  providerStatus: 'unknown',
  lastErrorCode: 'TIMEOUT',
  errorMessage: '核对后再操作',
  chargeStatus: 'UNCERTAIN',
  submittedAt: '2026-11-20T10:01:00Z',
  updatedAt: '2026-11-20T10:02:00Z',
  reconciledAt: null,
  reconcileError: null,
  reservationId: 'reservation-1',
  reservationStatus: 'UNCERTAIN',
  reservationChargeStatus: 'UNCERTAIN',
  estimatedPoints: 1,
  actualPoints: null,
  reservationCreatedAt: '2026-11-20T10:00:00Z',
} satisfies OperationsIssueRecord

let view: Awaited<ReturnType<typeof mount>>
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.mocked(getOperationsIssues).mockResolvedValue({
    category: 'cloudUnknown', total: 1, limit: 20, offset: 0, items: [record],
  } satisfies OperationsIssuePage)
})
afterEach(async () => {
  await view?.unmount()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
})
afterAll(teardownReliabilityDom)

it('shows other-account problems in a read-only admin panel, not owner task links', async () => {
  view = await mount(<OperationsIssuesBrowser adminId="admin-id" category="cloudUnknown" onClose={() => {}} />)
  await act(async () => { await Promise.resolve() })
  expect(getOperationsIssues).toHaveBeenCalledWith('admin-id', 'cloudUnknown', 0, 20, '')
  expect(view.host.textContent).toContain('另一个品牌的异常视频')
  expect(view.host.textContent).toContain('Pagewings')
  expect(view.host.textContent).toContain('brand-account')
  expect(view.host.querySelector('a[href*="task="]')).toBeNull()
  const disclosure = view.host.querySelector<HTMLButtonElement>('.ops-issue-entry-main')
  expect(disclosure).not.toBeNull()
  await act(async () => { disclosure?.click() })
  expect(view.host.textContent).toContain('cloud-123')
  expect(view.host.textContent).toContain('TIMEOUT')
  expect(view.host.textContent).toContain('不会修改任务、费用')
})
