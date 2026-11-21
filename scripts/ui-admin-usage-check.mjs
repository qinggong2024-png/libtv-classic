import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_USAGE_REPORT_DIR, 'Set PLAYWRIGHT_MODULE and UI_USAGE_REPORT_DIR outside the worktrees')
const reportDir = resolve(process.env.UI_USAGE_REPORT_DIR)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const html = '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">'
  + "import React from 'react'; import {createRoot} from 'react-dom/client';"
  + "import {AdminCenterView} from '/src/features/admin/AdminCenterView.tsx'; import {AccountUsagePanel} from '/src/features/settings/AccountUsagePanel.tsx'; import '/src/styles.css';"
  + "const mode=new URLSearchParams(location.search).get('mode')||'admin';"
  + "function Fixture(){const [member,setMember]=React.useState(mode!=='admin');const [section,setSection]=React.useState('accounts');"
  + "const actor=member?{userId:'member',username:mode==='other-admin'?'other-admin':'member',role:mode==='other-admin'?'ADMIN':'USER',isBrandAdmin:mode==='brand-admin'}:{userId:'root',username:'classic-admin',role:'ADMIN'};"
  + "return React.createElement('main',{style:{padding:'20px',maxWidth:'1440px',margin:'auto',minWidth:0}},mode==='personal'?React.createElement(AccountUsagePanel,{actorId:'member',personal:true}):React.createElement(React.Fragment,null,mode==='admin'?React.createElement('button',{onClick:()=>setMember(true)},'切换普通账号（测试）'):null,React.createElement(AdminCenterView,{actor,system:{sharedAuthentication:true,environment:'test'},section,onSectionChange:setSection,onKlingChanged:async()=>{}})));}"
  + "createRoot(document.getElementById('root')).render(React.createElement(Fixture));</script></body></html>"
const brands = [{ id: 'a', name: '品牌 A' }, { id: 'b', name: '品牌 B' }]
const users = [
  { id: 'root', username: 'classic-admin', role: 'ADMIN', brandId: null, isActive: true, dailyBudgetUsd: null },
  { id: 'member', username: 'designer-a', role: 'USER', brandId: 'a', isActive: true, dailyBudgetUsd: 12 },
  { id: 'other', username: 'designer-b', role: 'USER', brandId: 'b', isActive: false, dailyBudgetUsd: 0 },
].map((user) => ({ ...user, isBrandAdmin: false, pagePermissions: ['images', 'make', 'records'], createdAt: '2026-10-15T01:00:00Z' }))
const record = (id, amount, status = 'ESTIMATED', extra = {}) => ({ id, operationId: id, userId: 'member', username: 'designer-a', kind: 'IMAGE', category: 'IMAGE', units: 2, amountUsd: amount, status, day: '2026-10-19', createdAt: '2026-10-19T01:00:00Z', ...extra })
const records = [record('clip', .2, 'ESTIMATED', { kind: 'CUTFLOW', category: 'CUTFLOW', units: 2 }), ...Array.from({ length: 51 }, (_, index) => record('image-' + index, .000001)), record('reserved', 1.2, 'RESERVED', { kind: 'VIDEO', category: 'VIDEO', units: 6 }), record('uncertain', .8, 'UNCERTAIN', { day: '2026-10-18', kind: 'VIDEO', category: 'DEPTH_VIDEO', units: 6 }), record('released', .5, 'RELEASED'), record('unpriced', null, 'UNCERTAIN', { kind: 'VIDEO', category: null, units: 1 }), record('other-image', .4, 'ESTIMATED', { userId: 'other', username: 'designer-b' })]
function totals(items) {
  const sum = (status) => items.filter((item) => status === 'OCCUPIED' ? item.status !== 'RELEASED' : item.status === status).reduce((value, item) => value + Math.round((item.amountUsd ?? 0) * 1e6), 0) / 1e6
  return { recordCount: items.length, imageCount: items.filter((item) => item.kind === 'IMAGE').reduce((sum, item) => sum + item.units, 0), videoSeconds: items.filter((item) => item.kind === 'VIDEO' && item.category).reduce((sum, item) => sum + item.units, 0), cutflowClips: items.filter((item) => item.kind === 'CUTFLOW').reduce((sum, item) => sum + item.units, 0), unknownVideoCount: items.filter((item) => item.kind === 'VIDEO' && !item.category).length, estimatedUsd: sum('ESTIMATED'), reservedUsd: sum('RESERVED'), uncertainUsd: sum('UNCERTAIN'), releasedUsd: sum('RELEASED'), budgetUsedUsd: sum('OCCUPIED'), unpricedCount: items.filter((item) => item.status !== 'RELEASED' && item.amountUsd === null).length }
}
function fixture(params, personal) {
  const start = params.get('startDate') || '2026-10-19', end = params.get('endDate') || '2026-10-19'
  const scoped = users.filter((user) => (!personal || user.id === 'member') && (!params.get('userId') || user.id === params.get('userId')) && (!params.get('brandId') || user.brandId === params.get('brandId')) && user.username.toLowerCase().includes((params.get('search') || '').toLowerCase()))
  const ids = new Set(scoped.map((user) => user.id)), items = records.filter((item) => ids.has(item.userId) && item.day >= start && item.day <= end)
  const filtered = items.filter((item) => (!params.get('status') || (params.get('status') === 'OCCUPIED' ? item.status !== 'RELEASED' : item.status === params.get('status'))) && (!params.get('kind') || item.kind === params.get('kind')) && (params.get('unpricedOnly') !== 'true' || (item.status !== 'RELEASED' && item.amountUsd === null)))
  const offset = Number(params.get('offset') || 0), limit = Number(params.get('limit') || 50), summaryOffset = Number(params.get('summaryOffset') || 0), summaryLimit = Number(params.get('summaryLimit') || 100)
  return { day: end, startDate: start, endDate: end, budgetDay: '2026-10-19', currency: 'USD', ...totals(items), total: filtered.length, items: filtered.slice(offset, offset + limit), events: [], eventTotal: 0, summaryTotal: scoped.length, policies: [], summaries: scoped.slice(summaryOffset, summaryOffset + summaryLimit).map((user) => {
    const today = totals(records.filter((item) => item.userId === user.id && item.day === '2026-10-19'))
    return { userId: user.id, username: user.username, brandId: user.brandId, isActive: user.isActive, ...totals(items.filter((item) => item.userId === user.id)), dailyBudgetUsd: user.dailyBudgetUsd, todayBudgetUsedUsd: today.budgetUsedUsd, todayUnpricedCount: today.unpricedCount, todayRemainingUsd: user.dailyBudgetUsd === null || today.unpricedCount ? null : Math.max(0, user.dailyBudgetUsd - today.budgetUsedUsd) }
  }) }
}
const server = await createServer({ configFile: false, envFile: false, cacheDir: resolve(reportDir, 'vite-cache'), root: resolve(root, 'frontend'), server: { host: '127.0.0.1', port: 0 }, logLevel: 'error', plugins: [{ name: 'usage-fixture', configureServer(vite) {
  vite.middlewares.use('/__usage_fixture', async (_request, response, next) => { try { response.setHeader('Content-Type', 'text/html'); response.end(await vite.transformIndexHtml('/__usage_fixture', html)) } catch (error) { next(error) } })
} }] })
const report = { checks: [], errors: [], externalRequests: [], writes: [], requests: [] }
let browser
try {
  await server.listen()
  const origin = 'http://127.0.0.1:' + server.httpServer.address().port
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const mode of ['admin', 'personal', 'member', 'brand-admin', 'other-admin']) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } }), page = await context.newPage()
    await page.clock.install({ time: new Date('2026-10-19T02:00:00Z') })
    let failNext = false
    const requestStart = report.requests.length
    page.on('pageerror', (error) => report.errors.push(error.message))
    await page.route('**/*', async (route) => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== origin) { report.externalRequests.push(url.origin); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      report.requests.push({ mode, path: url.pathname, ...Object.fromEntries(url.searchParams) })
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
      if (url.pathname === '/api/v1/admin/users') return route.fulfill({ json: users })
      if (url.pathname === '/api/v1/admin/brands') return route.fulfill({ json: brands })
      if (url.pathname === '/api/v1/admin/usage-pricing') return route.fulfill({ json: { version: 1, imagePriceUsd: .04, videoPricePerSecondUsd: .2, depthVideoPricePerSecondUsd: .1, recreateVideoPricePerSecondUsd: .2, cutflowPriceUsd: .1, legacyConflicts: [] } })
      if (['/api/v1/admin/account-usage', '/api/v1/account/usage'].includes(url.pathname)) {
        if (failNext) { failNext = false; return route.fulfill({ status: 500, json: { detail: '合成读取失败' } }) }
        return route.fulfill({ json: fixture(url.searchParams, mode === 'personal') })
      }
      report.errors.push('Unmocked API ' + url.pathname)
      return route.fulfill({ status: 404, json: {} })
    })
    await page.goto(origin + '/__usage_fixture?mode=' + mode)
    if (!['admin', 'personal'].includes(mode)) {
      await page.getByText('没有管理中心权限', { exact: true }).waitFor()
      assert.equal(await page.getByRole('navigation', { name: '管理中心导航' }).count(), 0)
      assert.equal(report.requests.length, requestStart)
      report.checks.push({ mode, managementHidden: true, noAdminRequests: true }); await context.close(); continue
    }
    if (mode === 'personal') {
      const panel = page.getByRole('region', { name: '账号使用记录', exact: true })
      await panel.getByRole('heading', { name: '用量明细', exact: true }).waitFor()
      assert.equal(await panel.getByLabel('账号', { exact: true }).count(), 0)
      assert.equal(await panel.getByRole('region', { name: '账号用量汇总表' }).count(), 0)
      assert.equal(await panel.getByText('designer-b', { exact: true }).count(), 0)
      assert(report.requests.slice(requestStart).every((query) => query.path === '/api/v1/account/usage' && query.userId === 'member'))
      report.checks.push({ personal: true, ownRecordsOnly: true }); await context.close(); continue
    }
    await page.getByRole('tab', { name: '用量与费用', exact: true }).click()
    const panel = page.getByRole('region', { name: '账号使用记录', exact: true }), summary = panel.getByRole('region', { name: '账号用量汇总表', exact: true })
    await summary.locator('tbody tr').nth(2).waitFor()
    const row = summary.locator('tbody tr').filter({ hasText: 'designer-a' })
    const text = await summary.innerText()
    for (const label of ['已停用', '剩余待核实', '未设限额']) assert(text.includes(label))
    assert.equal(await panel.getByRole('button', { name: '本月', exact: true }).getAttribute('aria-pressed'), 'true')
    assert.equal(await panel.getByLabel('开始日期', { exact: true }).count(), 0)
    assert.equal(await row.locator('td').nth(2).innerText(), '2')
    const mainAmount = await panel.locator('.admin-usage-totals').innerText()
    await page.screenshot({ path: resolve(reportDir, 'usage-overview-1440.png') })
    await panel.getByRole('button', { name: '查看 designer-a 的用量明细', exact: true }).click()
    const dialog = page.getByRole('dialog'), ledger = dialog.getByRole('region', { name: '用量明细表' })
    await ledger.locator('tbody tr').nth(19).waitFor()
    await dialog.getByRole('button', { name: '用量明细下一页', exact: true }).click()
    await dialog.getByText('第 2 / 3 页 · 共 56 条', { exact: true }).waitFor()
    await dialog.getByLabel('用途', { exact: true }).selectOption('CUTFLOW')
    await ledger.locator('tbody tr').waitFor()
    assert.equal(await ledger.locator('tbody tr').count(), 1)
    assert((await ledger.innerText()).includes('送剪原片 2 条'))
    assert((await dialog.locator('.admin-usage-detail-total').innerText()).includes('2.200051'))
    await page.screenshot({ path: resolve(reportDir, 'usage-drawer-1440.png') })
    await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' })
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '查看 designer-a 的用量明细')
    assert.equal(await panel.locator('.admin-usage-totals').innerText(), mainAmount)
    await panel.getByRole('button', { name: '查看费用待确认明细', exact: true }).click()
    await ledger.locator('tbody tr').nth(1).waitFor()
    assert.equal(await dialog.getByLabel('费用状态', { exact: true }).inputValue(), 'UNCERTAIN')
    await dialog.getByRole('button', { name: '关闭', exact: true }).click()
    await panel.getByRole('button', { name: '查看记录', exact: true }).click()
    await ledger.locator('tbody tr').waitFor()
    assert.equal(await ledger.locator('tbody tr').count(), 1)
    assert((await ledger.innerText()).includes('时长未记录'))
    await page.keyboard.press('Escape')
    await panel.getByRole('button', { name: '本周', exact: true }).click(); await summary.locator('tbody tr').nth(2).waitFor()
    await panel.getByLabel('品牌', { exact: true }).selectOption('a'); await summary.locator('tbody tr').waitFor()
    assert.equal(await summary.locator('tbody tr').count(), 1)
    await panel.getByRole('button', { name: '查看 designer-a 的用量明细', exact: true }).click(); await ledger.locator('tbody tr').nth(19).waitFor()
    assert((await dialog.locator('.admin-usage-detail-scope').innerText()).includes('2026-10-19 至 2026-10-19 · 品牌 A'))
    await page.keyboard.press('Escape')
    assert.equal(await panel.getByLabel('品牌', { exact: true }).inputValue(), 'a')
    assert.equal(await panel.getByRole('button', { name: '本周', exact: true }).getAttribute('aria-pressed'), 'true')
    await panel.getByLabel('品牌', { exact: true }).selectOption(''); await summary.locator('tbody tr').nth(2).waitFor()
    await panel.getByLabel('搜索账号', { exact: true }).fill('no-such-account'); await panel.getByText('没有符合筛选条件的账号。', { exact: true }).waitFor()
    await panel.getByLabel('搜索账号', { exact: true }).fill('designer-b'); await summary.locator('tbody tr').waitFor()
    assert.equal(await summary.locator('tbody tr').count(), 1)
    await panel.getByLabel('搜索账号', { exact: true }).fill(''); await summary.locator('tbody tr').nth(2).waitFor()
    for (const width of [1440, 1280]) { await page.setViewportSize({ width, height: 1100 }); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); report.checks.push({ width, overflow: false }) }
    await panel.getByRole('button', { name: '自定义', exact: true }).click(); await summary.locator('tbody tr').nth(2).waitFor()
    const count = report.requests.length
    await panel.getByLabel('开始日期', { exact: true }).fill('2026-10-20'); await panel.getByRole('alert').waitFor()
    assert.equal(report.requests.length, count)
    await panel.getByLabel('结束日期', { exact: true }).fill('2026-10-21'); await summary.locator('tbody tr').nth(2).waitFor()
    assert.equal(await row.locator('td').nth(2).innerText(), '0')
    assert((await row.innerText()).includes('剩余待核实'), 'today quota must stay independent of the historical range')
    await panel.getByRole('button', { name: '查看全部明细', exact: true }).click(); await dialog.getByText('所选区间及筛选条件没有用量记录。', { exact: true }).waitFor(); await page.keyboard.press('Escape')
    failNext = true
    await panel.getByRole('button', { name: '刷新记录', exact: true }).click(); await panel.getByRole('alert').waitFor()
    assert.equal(await summary.count(), 0)
    await panel.getByRole('button', { name: '刷新记录', exact: true }).click(); await summary.locator('tbody tr').nth(2).waitFor()
    assert.equal(await panel.getByLabel('开始日期', { exact: true }).inputValue(), '2026-10-20')
    await page.getByRole('button', { name: '切换普通账号（测试）' }).click(); await page.getByText('没有管理中心权限', { exact: true }).waitFor()
    assert.equal(await summary.count(), 0); assert.equal(await page.getByRole('dialog').count(), 0)
    report.checks.push({ admin: true, separateUnits: true, drawerPagination: true, filtersPreserved: true, search: true, unpriced: true, todayBudgetIndependent: true, customValidation: true, empty: true, retry: true, switchClearsAdminData: true })
    await context.close()
  }
  assert.deepEqual(report.errors, []); assert.deepEqual(report.externalRequests, []); assert.deepEqual(report.writes, [])
  console.log(JSON.stringify({ checks: report.checks, requests: report.requests.length, errors: report.errors }))
} finally {
  await writeFile(resolve(reportDir, 'usage-ui-report.json'), JSON.stringify(report, null, 2))
  await browser?.close(); await server.close()
}
