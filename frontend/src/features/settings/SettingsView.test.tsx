// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsView } from './SettingsView'
import { getAccountUsage, getPersonalPromptAutoConfirmation, getPersonalVideoLearning } from '../../api'
import type { AccountUsageReport, SystemStatus, VideoLearningPreference } from '../../types'

vi.mock('../../api', () => ({
  getAccountUsage: vi.fn(),
  getPersonalPromptAutoConfirmation: vi.fn(),
  getPersonalVideoLearning: vi.fn(),
  setPersonalPromptAutoConfirmation: vi.fn(),
  setPersonalVideoLearning: vi.fn(),
  clearPersonalVideoLearning: vi.fn(),
  changePassword: vi.fn(),
  ApiError: Error,
}))
const totals = { recordCount: 1, imageCount: 1, videoSeconds: 0, cutflowClips: 0, originalDownloads: 0, cutflowDownloads: 0, unknownVideoCount: 0, estimatedUsd: 2, reservedUsd: 1, uncertainUsd: 0, releasedUsd: 5, unpricedCount: 0, budgetUsedUsd: 3, imageUsedUsd: 1, videoUsedUsd: 2, cutflowUsedUsd: 0, otherUsedUsd: 0 }
const summary = { ...totals, userId: 'user-1', username: 'member', brandId: null, isActive: true, dailyBudgetUsd: 10, todayBudgetUsedUsd: 3, todayRemainingUsd: 7, todayUnpricedCount: 0 }
const report: AccountUsageReport = { ...totals, day: '2026-11-24', startDate: '2026-11-24', endDate: '2026-11-24', budgetDay: '2026-11-24', currency: 'USD', total: 1, summaries: [summary], summaryTotal: 1, policies: [], items: [{ id: 'usage-1', username: 'member', kind: 'IMAGE', category: 'IMAGE', operationId: 'task-1', units: 1, amountUsd: 1, status: 'ESTIMATED', createdAt: '2026-11-24T04:00:00Z' }], events: [], eventTotal: 0 }
let root: Root
const onLogout = vi.fn()
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.mocked(getAccountUsage).mockReset().mockResolvedValue(report)
  vi.mocked(getPersonalPromptAutoConfirmation).mockResolvedValue({ automatic: false } as Awaited<ReturnType<typeof getPersonalPromptAutoConfirmation>>)
  vi.mocked(getPersonalVideoLearning).mockResolvedValue({ enabled: false, feedbackCount: 0, scopeCount: 0 } as VideoLearningPreference)
  document.body.innerHTML = '<div id="root"></div>'
  root = createRoot(document.getElementById('root')!)
})
afterEach(async () => { await act(async () => root.unmount()); vi.unstubAllGlobals() })
const render = async (actorId = 'user-1') => {
  await act(async () => root.render(<SettingsView actorId={actorId} system={{ sharedAuthentication: true } as SystemStatus} onLogout={onLogout} browserNotificationsEnabled={false} browserNotificationPermission="default" onEnableBrowserNotifications={async () => {}} onDisableBrowserNotifications={() => {}} />))
}
const click = async (label: string) => { await act(async () => Array.from(document.querySelectorAll('button')).find(button => button.textContent === label)!.click()) }
const panel = () => document.querySelector('.personal-usage')!
describe('one-stop personal settings', () => {
  it('shows used amount, actual remaining quota, categories and recent transactions directly', async () => {
    await render()
    expect(panel().textContent).toContain('今日已用')
    expect(panel().textContent).toContain('$3.00')
    expect(panel().textContent).toContain('剩余额度')
    expect(panel().textContent).toContain('$7.00')
    expect(panel().textContent).toContain('视频剪辑')
    expect(panel().textContent).toContain('费用分布')
    expect(panel().textContent).toContain('用量概览')
    expect(panel().textContent).toContain('用量明细')
    expect(document.querySelector('h1')?.textContent).toBe('个人设置')
    expect(document.querySelector('.personal-settings-sidebar')?.textContent).toContain('工作偏好')
    expect(document.querySelector('.personal-settings-sidebar')?.textContent).toContain('智能学习')
    expect(document.querySelector('.personal-settings-sidebar')?.textContent).toContain('账号与安全')
    expect(panel().querySelector('table')).not.toBeNull()
    expect(panel().textContent).not.toMatch(/已预留|已释放|费用口径|账号操作记录/)
    expect(panel().querySelectorAll('input[type=date]')).toHaveLength(0)
    expect(getAccountUsage).toHaveBeenCalledWith(expect.objectContaining({ userId: 'user-1', status: 'OCCUPIED', includeEvents: false }), 'user-1')
    await click('自定义')
    expect(panel().querySelectorAll('input[type=date]')).toHaveLength(2)
    await click('本月')
    expect(panel().textContent).toContain('区间已用')
    expect(panel().textContent).toContain('剩余额度')
  })
  it('keeps common settings on the same page and password editing collapsed', async () => {
    await render()
    expect(document.querySelector('input[aria-label="浏览器通知"]')).not.toBeNull()
    expect(document.querySelector('.personal-settings-account details[open]')).toBeNull()
    expect(document.querySelector('.personal-settings-account input[type=password]')).not.toBeNull()
    await click('退出登录')
    expect(onLogout).toHaveBeenCalled()
  })
  it('does not display a false remaining balance when today has unpriced charges', async () => {
    vi.mocked(getAccountUsage).mockResolvedValue({ ...report, unpricedCount: 1, summaries: [{ ...summary, todayUnpricedCount: 1, todayRemainingUsd: null }] })
    await render()
    expect(panel().textContent).toContain('待核对')
    expect(panel().textContent).not.toContain('$7.00')
  })
  it('supports unlimited budgets and does not invent category totals for older servers', async () => {
    vi.mocked(getAccountUsage).mockResolvedValue({ ...report, imageUsedUsd: undefined, videoUsedUsd: undefined, cutflowUsedUsd: undefined, summaries: [{ ...summary, dailyBudgetUsd: null, todayRemainingUsd: null }] })
    await render()
    expect(panel().textContent).toContain('不限额')
    expect(panel().textContent).toContain('分类金额暂未更新')
  })
  it('rejects reports without the current actor summary', async () => {
    vi.mocked(getAccountUsage).mockResolvedValue({ ...report, summaries: [{ ...summary, userId: 'another-user' }] })
    await render()
    expect(panel().textContent).toContain('无法读取个人额度')
    expect(panel().textContent).not.toContain('$3.00')
  })
})
