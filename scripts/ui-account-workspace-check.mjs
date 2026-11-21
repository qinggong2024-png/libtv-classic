import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startClassicServer } from './classic_web_server.mjs'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_ACCOUNT_WORKSPACE_REPORT_DIR, 'Set Playwright and external report directory')
const output = resolve(process.env.UI_ACCOUNT_WORKSPACE_REPORT_DIR)
for (const tree of [root, resolve(root, '../libtv-classic-0e3d1c8-main')]) {
  const path = relative(tree, output)
  assert(path.startsWith('..') || isAbsolute(path), 'Keep evidence outside the worktrees')
}
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const brands = [{ id: 'a', name: 'Brand A' }, { id: 'b', name: 'Brand B' }]
const users = Array.from({ length: 63 }, (_, index) => ({
  id: index === 0 ? '00000000-0000-0000-0000-000000000099' : `user-${index}`,
  username: index === 0 ? 'classic-admin' : `designer-${String(index).padStart(2, '0')}`,
  role: index === 0 ? 'ADMIN' : 'USER', isActive: index % 7 !== 0 || index === 0,
  brandId: index === 0 ? null : index % 2 ? 'a' : 'b',
  isBrandAdmin: false, dailyBudgetUsd: 10,
  pagePermissions: ['images', 'make', 'records'], createdAt: '2026-10-19T01:00:00Z',
}))
const empty = { recordCount: 0, imageCount: 0, videoSeconds: 0, unknownVideoCount: 0,
  unpricedCount: 0, budgetUsedUsd: 0, estimatedUsd: 0, reservedUsd: 0, uncertainUsd: 0, releasedUsd: 0 }
const summaries = users.map((user, index) => ({
  ...empty, userId: user.id, username: user.username, brandId: user.brandId, isActive: user.isActive,
  budgetUsedUsd: index / 100, estimatedUsd: index / 100, recordCount: index, imageCount: index,
  dailyBudgetUsd: 10, todayBudgetUsedUsd: index / 100, todayUnpricedCount: 0, todayRemainingUsd: 10 - index / 100,
}))
const events = Array.from({ length: 65 }, (_, index) => ({
  id: `event-${index}`, userId: users[index % 3 + 1].id, username: users[index % 3 + 1].username,
  action: index % 2 ? 'LOGIN' : 'UPDATE_ACCOUNT_POLICY', entityType: 'user',
  entityId: users[index % 3 + 1].id, createdAt: new Date(Date.UTC(2026, 9, 22, 6, 59 - index)).toISOString(),
}))
const records = Array.from({ length: 65 }, (_, index) => ({
  id: `record-${index}`, operationId: `record-${index}`, username: users[1].username,
  kind: 'IMAGE', category: 'IMAGE', units: 1, amountUsd: .01,
  status: index % 2 ? 'ESTIMATED' : 'UNCERTAIN', createdAt: '2026-10-22T01:00:00Z',
}))
const scopedUsers = params => users.filter(user => (!params.get('userId') || user.id === params.get('userId')) && (!params.get('brandId') || user.brandId === params.get('brandId')))
function usage(params) {
  const ids = new Set(scopedUsers(params).map(user => user.id))
  const rows = summaries.filter(row => ids.has(row.userId))
  const sum = rows.reduce((value, row) => value + Math.round(row.budgetUsedUsd * 1e6), 0) / 1e6
  const offset = Number(params.get('offset') || 0), limit = Number(params.get('limit') || 50)
  const summaryOffset = Number(params.get('summaryOffset') || 0), summaryLimit = Number(params.get('summaryLimit') || 100)
  const details = ids.has(users[1].id) ? records.filter(record => !params.get('status') || params.get('status') === record.status || params.get('status') === 'OCCUPIED') : []
  return { ...empty, day: '2026-10-22', budgetDay: '2026-10-22',
    startDate: params.get('startDate'), endDate: params.get('endDate'), currency: 'USD',
    budgetUsedUsd: sum, estimatedUsd: sum, total: details.length, recordCount: details.length,
    summaryTotal: rows.length, summaries: rows.slice(summaryOffset, summaryOffset + summaryLimit),
    items: details.slice(offset, offset + limit), policies: [], events: [], eventTotal: 0,
  }
}
function eventReport(params) {
  const ids = new Set(scopedUsers(params).map(user => user.id))
  const rows = events.filter(event => ids.has(event.userId) && (!params.get('action') || event.action === params.get('action')))
  const offset = Number(params.get('offset') || 0), limit = Number(params.get('limit') || 20)
  return { total: rows.length, items: rows.slice(offset, offset + limit), startDate: params.get('startDate'), endDate: params.get('endDate') }
}
const server = await startClassicServer({ root: process.env.UI_ACCOUNT_WORKSPACE_DIST || resolve(root, 'frontend/dist'), hosts: ['127.0.0.1'], port: 0, apiPort: 1 })
const origin = `http://127.0.0.1:${server.port}`
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const report = { checks: [], errors: [], writes: [], unmatched: [], requests: [] }
await mkdir(output, { recursive: true })
try {
  for (const width of [1576, 1024, 726, 390, 320]) {
    const page = await browser.newPage({ viewport: { width, height: 884 }, serviceWorkers: 'block', reducedMotion: 'reduce' })
    await page.clock.install({ time: new Date('2026-10-22T06:00:00Z') })
    page.on('pageerror', error => report.errors.push(error.message))
    const fixtures = createPageResponses({ empty: true })
    fixtures.set('/api/v1/auth/me', { userId: users[0].id, username: 'classic-admin', role: 'ADMIN' })
    fixtures.set('/api/v1/cutflow-handoffs/sites', { sites: [], defaultProfileId: null })
    fixtures.set('/api/v1/library/kling/elements', { items: [], pageNum: 1, pageSize: 100, total: 0, official: false })
    fixtures.set('/api/v1/image-generation-batches', { items: [], total: 0, nextOffset: 0, hasMore: false })
    let failEvents = false
    await page.route('**/*', route => {
      const request = route.request(), url = new URL(request.url()), params = url.searchParams
      if (url.origin !== origin) { report.unmatched.push(url.origin); return route.abort() }
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
      if (url.pathname.startsWith('/api/')) report.requests.push({ width, path: url.pathname, ...Object.fromEntries(params) })
      if (url.pathname === '/api/v1/admin/users') return route.fulfill({ json: users })
      if (url.pathname === '/api/v1/admin/brands') return route.fulfill({ json: brands })
      if (url.pathname === '/api/v1/account/usage') return route.fulfill({ json: usage(params) })
      if (url.pathname === '/api/v1/account/events') {
        if (failEvents) { failEvents = false; return route.fulfill({ status: 500, json: { detail: 'Synthetic read failure' } }) }
        return route.fulfill({ json: eventReport(params) })
      }
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-22', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0, unpricedCount: 0, prices: { imagePriceUsd: .1 } } })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      if (url.pathname.startsWith('/api/')) { report.unmatched.push(url.pathname); return route.fulfill({ status: 404, json: {} }) }
      return route.continue()
    })
    await page.goto(`${origin}/?view=admin&section=accounts`)
    await page.getByRole('heading', { name: '账号管理', exact: true }).waitFor()
    await page.locator('.account-row').nth(19).waitFor()
    assert.equal(await page.locator('.account-row').count(), 20)
    assert.equal(report.requests.filter(item => item.width === width && /account\/(usage|events)/.test(item.path)).length, 0, 'Inactive reports do not load')
    await page.getByRole('button', { name: '账号列表下一页', exact: true }).click()
    await page.locator('.account-row').filter({ hasText: 'designer-20' }).waitFor()
    await page.getByLabel('搜索账号', { exact: true }).fill('designer-62')
    assert.equal(await page.locator('.account-row').count(), 1)
    assert.equal(await page.getByRole('button', { name: '账号列表上一页' }).isDisabled(), true)
    await page.getByLabel('搜索账号', { exact: true }).fill('')
    await page.getByRole('button', { name: '创建账号', exact: true }).click()
    const drawer = page.getByRole('dialog', { name: '创建账号', exact: true })
    await drawer.waitFor()
    assert.equal(await drawer.getByLabel('登录账号').count(), 1)
    await page.screenshot({ path: resolve(output, `create-${width}.png`), animations: 'disabled' })
    await page.keyboard.press('Escape')
    await drawer.waitFor({ state: 'detached' })
    assert.equal(await page.getByRole('button', { name: '创建账号', exact: true }).evaluate(el => el === document.activeElement), true)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.screenshot({ path: resolve(output, `accounts-${width}.png`) })

    await page.getByRole('tab', { name: '用量与费用', exact: true }).click()
    const panel = page.getByRole('region', { name: '账号使用记录', exact: true })
    const summary = panel.getByRole('region', { name: '账号金额汇总表', exact: true })
    await summary.locator('tbody tr').nth(19).waitFor()
    const total = await panel.locator('.usage-totals > div:first-child dd').innerText()
    await panel.getByRole('button', { name: '账号汇总下一页', exact: true }).click()
    await summary.getByRole('button', { name: '查看 designer-20 的全部生成明细', exact: true }).waitFor()
    assert.equal(await panel.locator('.usage-totals > div:first-child dd').innerText(), total)
    await panel.getByLabel('账号汇总每页条数', { exact: true }).selectOption('10')
    await summary.getByRole('button', { name: '查看 classic-admin 的全部生成明细', exact: true }).waitFor()
    assert.equal(await summary.locator('tbody tr').count(), 10)
    await panel.getByRole('button', { name: '展开 designer-01 的金额拆分', exact: true }).click()
    await panel.getByRole('button', { name: '查看 designer-01 的已预留明细', exact: true }).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.screenshot({ path: resolve(output, `usage-${width}.png`) })
    await panel.getByRole('button', { name: '查看 designer-01 的全部生成明细', exact: true }).click()
    await panel.getByRole('heading', { name: '生成明细 · designer-01', exact: true }).waitFor()
    assert.equal(await summary.count(), 0)
    const details = panel.getByRole('region', { name: '生成明细表', exact: true })
    assert.equal(await details.locator('tbody tr').count(), 10)
    await panel.getByRole('button', { name: '生成明细下一页', exact: true }).click()
    await panel.getByRole('navigation', { name: '生成明细分页', exact: true }).getByText('第 2 / 7 页 · 共 65 条', { exact: true }).waitFor()
    await panel.getByRole('button', { name: '返回账号汇总', exact: true }).click()
    await summary.waitFor()
    await panel.getByLabel('品牌', { exact: true }).selectOption('b')
    await summary.getByRole('button', { name: '查看 designer-02 的全部生成明细', exact: true }).waitFor()
    assert.equal(await panel.getByRole('option', { name: 'designer-01', exact: true }).count(), 0)

    await page.getByRole('tab', { name: '操作记录', exact: true }).click()
    const log = page.getByRole('region', { name: '账号操作记录', exact: true })
    const logTable = log.getByRole('region', { name: '操作记录表', exact: true })
    await logTable.locator('tbody tr').nth(19).waitFor()
    await log.getByRole('button', { name: '操作记录下一页', exact: true }).click()
    await log.getByRole('navigation', { name: '操作记录分页' }).getByText('第 2 / 4 页 · 共 65 条', { exact: true }).waitFor()
    await log.getByRole('button', { name: '操作记录下一页', exact: true }).click()
    await log.getByRole('navigation', { name: '操作记录分页' }).getByText('第 3 / 4 页 · 共 65 条', { exact: true }).waitFor()
    await log.getByRole('button', { name: '操作记录下一页', exact: true }).click()
    await log.getByRole('navigation', { name: '操作记录分页' }).getByText('第 4 / 4 页 · 共 65 条', { exact: true }).waitFor()
    assert.equal(await logTable.locator('tbody tr').count(), 5)
    assert.equal(await log.getByRole('button', { name: '操作记录下一页' }).isDisabled(), true)
    await log.getByLabel('操作类型', { exact: true }).selectOption('LOGIN')
    await log.getByRole('navigation', { name: '操作记录分页' }).getByText('第 1 / 2 页 · 共 32 条', { exact: true }).waitFor()
    assert.equal(await logTable.getByRole('cell', { name: '登录', exact: true }).count(), 20)
    await logTable.locator('summary').first().click()
    await logTable.locator('details[open]').getByText('LOGIN', { exact: true }).scrollIntoViewIfNeeded()
    await logTable.locator('details[open]').getByText('LOGIN', { exact: true }).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.screenshot({ path: resolve(output, `events-${width}.png`) })
    await log.getByLabel('操作账号', { exact: true }).selectOption(users[4].id)
    await log.getByText('所选条件下没有操作记录。', { exact: true }).waitFor()
    failEvents = true
    await log.getByRole('button', { name: '刷新记录', exact: true }).click()
    await log.getByRole('alert').waitFor()
    await log.getByRole('button', { name: '刷新记录', exact: true }).click()
    await log.getByText('所选条件下没有操作记录。', { exact: true }).waitFor()
    await log.getByLabel('统计范围', { exact: true }).selectOption('custom')
    await log.getByText('所选条件下没有操作记录。', { exact: true }).waitFor()
    const count = report.requests.length
    await log.getByLabel('开始日期', { exact: true }).fill('2026-10-23')
    await log.getByRole('alert').waitFor()
    assert.equal(report.requests.length, count, 'Invalid date range does not query')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.getByRole('tab', { name: '操作记录', exact: true }).focus()
    await page.keyboard.press('Home')
    assert.equal(await page.getByRole('tab', { name: '账号管理', exact: true }).getAttribute('aria-selected'), 'true')
    report.checks.push({ width, accountSearch: true, createDrawer: true, summaryPaging: true, totalsStable: true, drill: true, eventsBeyond50: true, filters: true, empty: true, retry: true, keyboard: true, overflow: false })
    await page.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.writes, [])
  assert.deepEqual(report.unmatched, [])
  console.log(JSON.stringify({ checks: report.checks, requests: report.requests.length }))
} catch (error) {
  for (const context of browser.contexts()) for (const page of context.pages()) {
    console.error((await page.locator('body').innerText()).slice(0, 1800))
    await page.screenshot({ path: resolve(output, 'failure.png') })
  }
  throw error
} finally {
  await browser.close()
  await server.close()
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2))
}
