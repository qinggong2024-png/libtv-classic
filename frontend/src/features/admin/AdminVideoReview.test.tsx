import { act } from 'react'
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getAdminVideos } from '../../api'
import type { AdminVideoPage } from '../../types'
import { mount } from '../../test-helpers/reactHarness'
import { teardownReliabilityDom } from '../../test-helpers/reliabilityDom.mjs'
import { AdminVideoReview } from './AdminVideoReview'

vi.mock('../../api', async original => ({
  ...await original<typeof import('../../api')>(),
  getAdminVideos: vi.fn(),
}))
const data: AdminVideoPage = {
  days: 7, total: 1, offset: 0, limit: 24,
  items: [{
    artifactId: 'artifact-safe-id',
    attemptId: 'attempt-safe-id',
    taskId: 'owner-task-id',
    title: '其他账号生成的原片',
    username: 'brand-user',
    brandId: 'brand-id',
    brandName: 'Pagewings',
    createdAt: '2026-11-21T12:20:00Z',
    durationSeconds: 9.5,
    width: 1080,
    height: 1920,
    variant: 'STABLE',
    provider: 'kling',
    validationStatus: 'PASSED',
    finalApprovalStatus: 'PENDING',
  }],
}
let view: Awaited<ReturnType<typeof mount>>
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.mocked(getAdminVideos).mockResolvedValue(data)
})
afterEach(async () => {
  await view?.unmount()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
})
afterAll(teardownReliabilityDom)

it('shows an owner-labelled video and a review control without any editing action', async () => {
  view = await mount(<AdminVideoReview adminId="administrator-id" />)
  await act(async () => { await Promise.resolve() })
  expect(getAdminVideos).toHaveBeenCalledWith('administrator-id', 7, 0, '')
  expect(view.host.textContent).toContain('其他账号生成的原片')
  expect(view.host.textContent).toContain('Pagewings · brand-user')
  expect(view.host.querySelectorAll('.admin-video-card')).toHaveLength(1)
  expect(view.host.querySelector('.admin-video-card img')?.getAttribute('src')).toContain('artifact-safe-id/thumbnail')
  expect(view.host.textContent).toContain('只读巡检')
})
