import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getAccountUsage, getGlobalUsagePricing, login, saveGlobalUsagePricing, setApiUserId, updateAccountPolicy } from './api'

describe('brand account budget API contracts', () => {
  beforeEach(() => { vi.stubGlobal('window', globalThis); vi.stubGlobal('fetch', vi.fn()); setApiUserId('member') })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
  it('saves the account budget separately with authenticated CSRF writes', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ csrfToken: 'budget-csrf' })).mockResolvedValueOnce(Response.json({ id: 'member' }))
    await login('fixture', 'synthetic-test-password')
    await updateAccountPolicy('manager', 'member', { dailyBudgetUsd: 0, pagePermissions: ['images'] })
    const [url, init] = vi.mocked(fetch).mock.calls[1]
    expect(url).toBe('/api/v1/admin/users/member/policy')
    expect(JSON.parse(String(init?.body))).toEqual({ dailyBudgetUsd: 0, pagePermissions: ['images'] })
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('budget-csrf')
    expect(new Headers(init?.headers).get('X-User-Id')).toBe('manager')
  })
  it('loads and saves shared prices with a version, precision, zero and null intact', async () => {
    const pricing = { version: 2, imagePriceUsd: .123456, videoPricePerSecondUsd: 0, depthVideoPricePerSecondUsd: null, recreateVideoPricePerSecondUsd: .2, cutflowPriceUsd: .15 }
    vi.mocked(fetch).mockImplementation(async () => Response.json(pricing))
    await getGlobalUsagePricing('root')
    await saveGlobalUsagePricing('root', pricing)
    const [url, init] = vi.mocked(fetch).mock.calls[1]
    expect(url).toBe('/api/v1/admin/usage-pricing')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual(pricing)
    expect(new Headers(init?.headers).get('X-User-Id')).toBe('root')
  })
  it('sends explicit account, brand and business-day filters without changing the session identity', async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ items: [] }))
    await getAccountUsage({ userId: 'user-a', brandId: 'brand-a', day: '2026-10-15', offset: 50 }, 'manager')
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toContain('userId=user-a&brandId=brand-a&day=2026-10-15&offset=50')
    expect(new Headers(init?.headers).get('X-User-Id')).toBe('manager')
  })
  it('keeps range, account, status and paging filters in the same read-only request', async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ items: [] }))
    await getAccountUsage({ userId: 'user-a', startDate: '2026-10-01', endDate: '2026-10-19', status: 'UNCERTAIN', offset: 50 }, 'root')
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    const params = new URL(String(url), 'http://fixture').searchParams
    expect(Object.fromEntries(params)).toEqual({ userId: 'user-a', startDate: '2026-10-01', endDate: '2026-10-19', status: 'UNCERTAIN', offset: '50' })
    expect(init?.method ?? 'GET').toBe('GET')
    expect(new Headers(init?.headers).get('X-User-Id')).toBe('root')
  })
})
