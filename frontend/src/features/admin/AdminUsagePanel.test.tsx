// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getAdminAccountUsage } from '../../api'
import type { AccountUsageReport, AuthUser } from '../../types'
import { AdminUsagePanel } from './AdminUsagePanel'
import { shanghaiToday } from '../settings/usagePeriod'

vi.mock('../../api', () => ({ getAdminAccountUsage: vi.fn() }))
const admin: AuthUser = { userId: 'root', username: 'classic-admin', role: 'ADMIN' }
const report: AccountUsageReport = { day: '2026-11-14', startDate: '2026-11-01', endDate: '2026-11-14', budgetDay: '2026-11-14', currency: 'USD', total: 0, recordCount: 0, imageCount: 0, videoSeconds: 0, cutflowClips: 2, originalDownloads: 0, cutflowDownloads: 0, unknownVideoCount: 0, estimatedUsd: .2, reservedUsd: 0, uncertainUsd: 0, releasedUsd: 0, unpricedCount: 0, budgetUsedUsd: .2, summaries: [], summaryTotal: 0, eventTotal: 0, policies: [], items: [], events: [] }
let root: Root

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.mocked(getAdminAccountUsage).mockReset().mockResolvedValue({ ...report, budgetDay: shanghaiToday() })
  document.body.innerHTML = '<div id="test-root"></div>'
  root = createRoot(document.getElementById('test-root')!)
})
afterEach(async () => { await act(async () => root.unmount()); vi.unstubAllGlobals() })
const render = async (actor: AuthUser) => { await act(async () => root.render(<AdminUsagePanel actor={actor} users={[]} brands={[]} />)) }

describe('administrator-only usage workspace', () => {
  it.each([
    { ...admin, username: 'member', role: 'USER' as const },
    { ...admin, username: 'brand-admin', role: 'USER' as const, isBrandAdmin: true },
    { ...admin, username: 'other-admin' },
    { ...admin, role: 'USER' as const },
  ])('does not render or request management data for $username / $role', async (actor) => {
    await render(actor)
    expect(document.body.textContent).toContain('没有管理中心权限')
    expect(document.querySelector('input,select,table')).toBeNull()
    expect(getAdminAccountUsage).not.toHaveBeenCalled()
  })

  it('clears administrator content immediately on account switching and ignores late data', async () => {
    let resolve!: (value: AccountUsageReport) => void
    vi.mocked(getAdminAccountUsage).mockReturnValueOnce(new Promise((done) => { resolve = done }))
    await render(admin)
    await render({ userId: 'member', username: 'member', role: 'USER' })
    await act(async () => resolve({ ...report, budgetUsedUsd: 98765 }))
    expect(document.body.textContent).toContain('没有管理中心权限')
    expect(document.body.textContent).not.toContain('98765')
    expect(document.querySelector('table')).toBeNull()
    expect(getAdminAccountUsage).toHaveBeenCalledTimes(1)
  })

  it('uses separate quantity columns, today defaults and the administrator endpoint', async () => {
    await render(admin)
    expect(document.body.textContent).toContain('预估费用')
    expect(document.body.textContent).not.toContain('区间占用')
    expect(Array.from(document.querySelectorAll('thead th')).map((cell) => cell.textContent)).toEqual(['账号 / 品牌', '图片（张）', '视频（秒）', '送剪原片（条）', '已下载原片（条）', '已下载 CUT 成片（条）', '预估费用', '今日剩余额度', '明细'])
    expect(getAdminAccountUsage).toHaveBeenCalledWith(expect.objectContaining({ includeEvents: false, startDate: shanghaiToday(), endDate: shanghaiToday() }), 'root')
    expect(document.querySelector('button[aria-pressed="true"]')?.textContent).toBe('今天')
  })

  it('uses the selected day rather than today for the quota and supports a historical range', async () => {
    vi.setSystemTime(new Date('2026-11-24T04:00:00Z'))
    try {
      vi.mocked(getAdminAccountUsage).mockImplementation(async (query) => {
        const used = query.startDate === query.endDate ? 35 : 90
        return { ...report, budgetDay: '2026-11-24', summaries: [{ ...report, userId: 'gc', username: 'GC', brandId: null, isActive: true, dailyBudgetUsd: 50, budgetUsedUsd: used, todayBudgetUsedUsd: 10, todayRemainingUsd: 40, todayUnpricedCount: 0 }] }
      })
      await render(admin)
      const click = async (label: string) => { await act(async () => Array.from(document.querySelectorAll('button')).find(button => button.textContent === label || button.getAttribute('aria-label') === label)!.click()) }
      const quota = () => document.querySelector('tbody tr td:nth-last-child(2)')?.textContent
      expect(quota()).toContain('US$40.00')
      await click('自定义')
      expect(document.querySelectorAll('input[type="date"]')).toHaveLength(0)
      await click('2026-11-18')
      expect(quota()).toContain('US$15.00')
      expect(quota()).not.toContain('US$40.00')
      await click('2026-11-20')
      expect(getAdminAccountUsage).toHaveBeenCalledWith(expect.objectContaining({ startDate: '2026-11-18', endDate: '2026-11-20' }), 'root')
      expect(getAdminAccountUsage).toHaveBeenCalledWith(expect.objectContaining({ startDate: '2026-11-20', endDate: '2026-11-20' }), 'root')
      expect(quota()).toContain('US$15.00')
      expect(document.querySelector('.admin-usage-footer')?.textContent).toContain('非当日留存快照')
      await click('选择统计日期范围')
      const first = document.querySelector('[aria-label="2026-11-17"]')!
      const last = document.querySelector('[aria-label="2026-11-19"]')!
      await act(async () => {
        first.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }))
        last.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0 }))
      })
      expect(getAdminAccountUsage).toHaveBeenCalledWith(expect.objectContaining({ startDate: '2026-11-17', endDate: '2026-11-19' }), 'root')
    } finally { vi.useRealTimers() }
  })

  it('presents pending results as an included estimate rather than an extra charge', async () => {
    vi.mocked(getAdminAccountUsage).mockResolvedValue({
      ...report,
      estimatedUsd: 750.0786,
      reservedUsd: 114.3,
      uncertainUsd: 43.6884,
      releasedUsd: 32.802,
      budgetUsedUsd: 908.067,
    })
    await render(admin)
    expect(document.querySelector('.admin-usage-totals')?.textContent).toContain('US$908.067')
    expect(document.querySelectorAll('.admin-usage-cost-breakdown > div')).toHaveLength(3)
    expect(document.querySelector('.admin-usage-cost-breakdown')?.textContent).toContain('US$43.6884')
    expect(document.querySelector('.admin-usage-definitions')?.textContent).toContain('不是额外收费')
    expect(document.querySelector('details.admin-usage-definitions')?.hasAttribute('open')).toBe(false)
    expect(document.querySelector('.admin-usage-definitions')?.textContent).toContain('不代表已实际扣款')
    expect(document.querySelector('.admin-usage-estimate-hint')?.textContent).toBe('预估 · 非实际扣费')
    expect(document.querySelector('.admin-usage-explainer')?.textContent).toContain('US$32.802')
    expect(document.querySelector('.admin-usage-totals')?.textContent).not.toContain('US$43.6884')
    expect(document.querySelector('.admin-usage-explainer')?.textContent).not.toContain('不是额外收费')
    const pending = document.querySelector<HTMLButtonElement>('button[aria-label="查看待核对预估 · 已计入合计明细"]')
    expect(pending).not.toBeNull()
    HTMLDialogElement.prototype.showModal = function () { this.open = true }
    HTMLDialogElement.prototype.close = function () { this.open = false }
    await act(async () => pending!.click())
    expect(getAdminAccountUsage).toHaveBeenCalledWith(expect.objectContaining({ status: 'UNCERTAIN' }), 'root')
  })

  it('does not retain financial rows after an authorization failure', async () => {
    await render(admin)
    vi.mocked(getAdminAccountUsage).mockRejectedValueOnce(new Error('没有管理中心权限'))
    const refresh = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes('刷新记录'))!
    await act(async () => refresh.click())
    expect(document.querySelector('[role="alert"]')).not.toBeNull()
    expect(document.querySelector('table')).toBeNull()
  })
})
