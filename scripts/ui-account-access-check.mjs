import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_ACCOUNT_REPORT_DIR, 'Set UI_ACCOUNT_REPORT_DIR outside the worktrees')
const reportDir = resolve(process.env.UI_ACCOUNT_REPORT_DIR)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import {AdminCenterView} from '/src/features/admin/AdminCenterView.tsx'; import '/src/styles.css';
const manager=new URLSearchParams(location.search).get('manager')==='1';
createRoot(document.getElementById('root')).render(React.createElement(AdminCenterView,{system:null,actor:{userId:manager?'manager':'root',username:manager?'brand-manager':'classic-admin',role:manager?'USER':'ADMIN',brandId:manager?'a':null,isBrandAdmin:manager},section:'accounts',onSectionChange:()=>{},onKlingChanged:async()=>{}}));
</script></body></html>`
const appHtml = html.replace("import {AdminCenterView} from '/src/features/admin/AdminCenterView.tsx';", "import App from '/src/app/App.tsx';").replace(/const manager=[\s\S]*?<\/script>/, "createRoot(document.getElementById('root')).render(React.createElement(App));</script>")
const server = await createServer({ configFile: false, envFile: false, cacheDir: resolve(reportDir, 'vite-cache'), root: resolve(root, 'frontend'), server: { host: '127.0.0.1', port: 0 }, logLevel: 'error', plugins: [{ name: 'account-fixture', configureServer(vite) {
  vite.middlewares.use('/__account_fixture', async (_req, res, next) => {
    try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__account_fixture', html)) }
    catch (error) { next(error) }
  })
  vite.middlewares.use('/__app_fixture', async (_req, res, next) => {
    try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__app_fixture', appHtml)) }
    catch (error) { next(error) }
  })
}}] })
const report = { checks: [], errors: [], externalRequests: [], writes: [] }
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const manager of [false, true]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
    const page = await context.newPage()
    page.on('pageerror', (error) => report.errors.push(error.message))
    page.on('console', (message) => { if (message.type() === 'error' && !message.text().includes('status of 409')) report.errors.push(message.text()) })
    const brands = [{ id: 'a', name: '品牌 A' }, { id: 'b', name: '品牌 B' }]
    const member = { id: 'member', username: 'designer-a', role: 'USER', isActive: true, brandId: 'a', isBrandAdmin: false, createdAt: '2026-10-15T01:00:00Z', dailyBudgetUsd: 12, imagePriceUsd: .04, videoPricePerSecondUsd: .08, depthVideoPricePerSecondUsd: .05, recreateVideoPricePerSecondUsd: .2, pagePermissions: ['images', 'make', 'records', 'library'] }
    const users = [member, { ...member, id: manager ? 'manager' : 'root', username: manager ? 'brand-manager' : 'classic-admin', role: manager ? 'USER' : 'ADMIN', isBrandAdmin: manager }]
    let pricing = { version: 1, imagePriceUsd: .04, videoPricePerSecondUsd: .08, depthVideoPricePerSecondUsd: .05, recreateVideoPricePerSecondUsd: .2, legacyConflicts: [] }
    let failPricingSave = true
    await page.route('**/*', async (route) => {
      const request = route.request(); const url = new URL(request.url())
      if (url.origin !== origin) { report.externalRequests.push(url.origin); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (request.method() !== 'GET') report.writes.push({ manager, method: request.method(), path: url.pathname })
      if (url.pathname === '/api/v1/admin/users') return route.fulfill({ json: users })
      if (url.pathname === '/api/v1/admin/brands') return route.fulfill({ json: manager ? brands.slice(0, 1) : brands })
      if (url.pathname === '/api/v1/admin/usage-pricing') {
        if (request.method() === 'PUT') {
          if (failPricingSave) { failPricingSave = false; return route.fulfill({ status: 409, json: { detail: '设置已更新，请刷新全站单价后重新保存' } }) }
          pricing = { ...request.postDataJSON(), version: pricing.version + 1, legacyConflicts: [] }
        }
        return route.fulfill({ json: pricing })
      }
      if (url.pathname === '/api/v1/admin/users/member/policy') {
        Object.assign(member, request.postDataJSON()); return route.fulfill({ json: member })
      }
      if (url.pathname === '/api/v1/account/usage') return route.fulfill({ json: {
        day: '2026-10-15', currency: 'USD', total: 4, estimatedUsd: .08, reservedUsd: 1.5, uncertainUsd: .8, budgetUsedUsd: 2.38, unpricedCount: 0,
        policies: [{ userId: 'member', username: 'designer-a', dailyBudgetUsd: member.dailyBudgetUsd }],
        items: [{ id: 'e1', username: 'designer-a', kind: 'IMAGE', operationId: 'fixture-batch', units: 2, amountUsd: .08, status: 'ESTIMATED', createdAt: '2026-10-15T01:00:00Z' }, { id: 'e2', username: 'designer-a', kind: 'VIDEO', operationId: 'fixture-attempt', units: 10, amountUsd: .8, status: 'UNCERTAIN', createdAt: '2026-10-15T01:00:00Z' }, { id: 'e3', username: 'designer-a', kind: 'VIDEO', category: 'DEPTH_VIDEO', operationId: 'fixture-depth', units: 6, amountUsd: .3, status: 'RESERVED', createdAt: '2026-10-15T01:00:00Z' }, { id: 'e4', username: 'designer-a', kind: 'VIDEO', category: 'RECREATE_VIDEO', operationId: 'fixture-recreate', units: 6, amountUsd: 1.2, status: 'RESERVED', createdAt: '2026-10-15T01:00:00Z' }],
        events: [{ id: 'login', username: 'designer-a', action: 'LOGIN', createdAt: '2026-10-15T01:00:00Z' }],
      } })
      report.errors.push(`Unmocked API ${request.method()} ${url.pathname}`)
      return route.fulfill({ status: 404, json: { detail: 'Synthetic test has no such API' } })
    })
    await page.goto(`${origin}/__account_fixture?manager=${manager ? 1 : 0}`)
    if (manager) {
      await page.getByText('管理中心仅允许 classic-admin 管理员访问。', { exact: true }).waitFor()
      assert.equal(await page.getByRole('form').count(), 0)
      assert.equal(await page.getByRole('button', { name: '权限与预算', exact: true }).count(), 0)
      report.checks.push({ manager, denied: true })
      await context.close()
      continue
    }
    await page.getByRole('heading', { name: '账号管理', exact: true }).waitFor()
    await page.getByRole('tab', { name: '用量与费用', exact: true }).click()
    await page.getByRole('button', { name: '生成明细', exact: true }).click()
    await page.getByRole('cell', { name: '10 秒', exact: true }).waitFor()
    await page.getByRole('cell', { name: '提取深度视频', exact: true }).waitFor()
    await page.getByRole('cell', { name: '人物／产品替换', exact: true }).waitFor()
    assert.equal(await page.getByRole('cell', { name: '6 秒', exact: true }).count(), 2)
    if (manager) {
      assert.equal(await page.getByRole('button', { name: '创建品牌', exact: true }).count(), 0)
      assert.equal(await page.getByRole('button', { name: /生成提供商/ }).count(), 0)
      assert.equal(await page.getByRole('button', { name: /运行总览/ }).count(), 0)
    }
    await page.getByRole('button', { name: '全站生成单价', exact: true }).click()
    const globalEditor = page.getByRole('form', { name: '全站生成单价', exact: true })
    assert.equal(await page.getByLabel('每张图片预估（USD）', { exact: true }).count(), 1)
    await globalEditor.getByLabel('每张图片预估（USD）').fill('0.123456')
    await globalEditor.getByLabel('每秒视频预估（USD）', { exact: true }).fill('0.25')
    await globalEditor.getByLabel('深度提取每秒预估（USD）').fill('0')
    await globalEditor.getByLabel('人物／产品替换每秒预估（USD）').fill('')
    await globalEditor.getByRole('button', { name: '保存全站单价' }).click()
    await globalEditor.getByRole('alert').waitFor()
    assert.equal(await globalEditor.getByLabel('每张图片预估（USD）').inputValue(), '0.123456')
    await globalEditor.getByRole('button', { name: '保存全站单价' }).click()
    await page.getByText('全站单价已保存，所有账号的新任务均使用此单价。', { exact: true }).waitFor()
    assert.equal(pricing.imagePriceUsd, .123456)
    assert.equal(pricing.videoPricePerSecondUsd, .25)
    assert.equal(pricing.depthVideoPricePerSecondUsd, 0)
    assert.equal(pricing.recreateVideoPricePerSecondUsd, null)
    await page.getByRole('button', { name: '刷新全站单价' }).click()
    assert.equal(await globalEditor.getByLabel('每张图片预估（USD）').inputValue(), '0.123456')
    await page.getByRole('tab', { name: '账号管理', exact: true }).click()
    await page.getByRole('button', { name: '权限与预算', exact: true }).click()
    const editor = page.getByRole('form', { name: 'designer-a 的权限与预算' })
    assert.equal(await editor.getByRole('spinbutton').count(), 1)
    await editor.getByLabel('每日预算（USD）').fill('20.125')
    await editor.getByRole('checkbox', { name: '视频制作', exact: true }).uncheck()
    await editor.getByRole('button', { name: '保存权限与预算' }).click()
    await page.getByText('账号“designer-a”的权限与预算已保存。', { exact: true }).waitFor()
    assert.equal(member.dailyBudgetUsd, 20.125)
    assert.equal(member.videoPricePerSecondUsd, .08)
    assert.equal(pricing.videoPricePerSecondUsd, .25)
    assert.deepEqual(member.pagePermissions, ['images', 'records'])
    await page.getByRole('button', { name: '权限与预算', exact: true }).click()
    assert.equal(await editor.getByRole('spinbutton').count(), 1)
    assert.equal(await editor.getByRole('checkbox', { name: '视频制作', exact: true }).isChecked(), false)
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1100 })
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
      assert.equal(overflow, false, `page overflow: ${manager ? 'manager' : 'platform'} ${width}`)
      await page.screenshot({ path: resolve(reportDir, `${manager ? 'brand-manager' : 'platform'}-${width}.png`), fullPage: true })
      report.checks.push({ manager, width, editor: 'saved', overflow })
    }
    await context.close()
  }
  for (const pages of [['images'], ['records'], []]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
    const page = await context.newPage()
    const requests = []
    page.on('pageerror', (error) => report.errors.push(error.message))
    await page.route('**/*', async (route) => {
      const request = route.request(); const url = new URL(request.url())
      if (url.origin !== origin) { report.externalRequests.push(url.origin); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      requests.push(url.pathname)
      assert.equal(request.method(), 'GET', 'App permission checks must be read-only')
      if (url.pathname === '/api/v1/system/status') return route.fulfill({ json: { environment: 'test', sharedAuthentication: true, videoGeneration: { enabled: false, available: false, authenticated: false, models: [] } } })
      if (url.pathname === '/api/v1/auth/me') return route.fulfill({ json: { userId: 'member', username: 'other-admin', role: 'ADMIN', pagePermissions: pages } })
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-16', currency: 'USD', dailyBudgetUsd: 12, budgetUsedUsd: 0, unpricedCount: 0, prices: { imagePriceUsd: .04, videoPricePerSecondUsd: .25, depthVideoPricePerSecondUsd: .05, recreateVideoPricePerSecondUsd: .2 } } })
      if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: [{ id: 'owned-task', title: '只读任务样例', phase: 'AWAITING_CONFIRMATION', stateVersion: 1, createdAt: '2026-10-15T01:00:00Z', updatedAt: '2026-10-15T01:00:00Z', assets: [], assetCount: 0, analysisResult: null, variants: [{ id: 'stable', kind: 'STABLE', promptText: 'Synthetic prompt', generationStatus: 'NOT_STARTED' }], actionCapabilities: { canCancel: true, canRegenerate: true, canConfirm: true }, generationProvider: 'kling' }] })
      if (url.pathname === '/api/v1/prompt-presets') return route.fulfill({ json: [] })
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { sites: [], defaultProfileId: null } })
      if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } })
      if (url.pathname === '/api/v1/auth/preferences/prompt-confirmation') return route.fulfill({ json: { automatic: false } })
      if (url.pathname === '/api/v1/auth/preferences/video-learning') return route.fulfill({ json: { enabled: false, totalEvents: 0, learnedTasks: 0, memoryCount: 0 } })
      if (url.pathname === '/api/v1/account/usage') return route.fulfill({ json: { day: '2026-10-15', total: 0, estimatedUsd: 0, reservedUsd: 0, uncertainUsd: 0, budgetUsedUsd: 0, unpricedCount: 0, policies: [], items: [], events: [] } })
      report.errors.push(`Unmocked App API ${url.pathname}`)
      return route.fulfill({ status: 404, json: {} })
    })
    await page.goto(`${origin}/__app_fixture?view=admin`)
    const expectedHeading = pages.includes('images') ? '图片中心' : pages.includes('records') ? '任务' : '偏好设置'
    await page.getByRole('heading', { name: expectedHeading, exact: true }).waitFor()
    const nav = page.locator('.sidebar')
    for (const [key, label] of [['images', '图片中心'], ['make', '视频制作'], ['records', '任务'], ['library', '内容库'], ['admin', '管理'], ['settings', '设置']]) {
      assert.equal(await nav.getByRole('button', { name: new RegExp(`^${label}(?:\\s*\\d+)?$`) }).count(), key === 'settings' || pages.includes(key) ? 1 : 0, `navigation ${key}`)
    }
    assert.equal(requests.some((path) => path.startsWith('/api/v1/admin/')), false)
    assert.equal(requests.includes('/api/v1/tasks'), pages.includes('records'))
    assert.equal(await page.getByRole('button', { name: /去创建任务|新建视频/ }).count(), 0)
    assert.equal(await page.locator('.service-banner').count(), 0)
    if (pages.includes('records')) {
      await page.getByRole('button', { name: /只读任务样例/ }).click()
      await page.getByRole('heading', { name: '确认视频方案', exact: true }).waitFor()
      assert.equal(await page.getByRole('textbox', { name: '最终提示词方案' }).isDisabled(), true)
      assert.equal(await page.getByRole('button', { name: /确认并|一键复用并生成|使用相同素材再制作/ }).count(), 0)
      assert.equal(await page.getByRole('button', { name: '取消任务', exact: true }).count(), 1)
    }
    await page.screenshot({ path: resolve(reportDir, `app-${pages[0] || 'settings'}.png`), fullPage: true })
    report.checks.push({ pages, forbiddenAdminFallback: expectedHeading, requests })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.externalRequests, [])
  assert.equal(report.writes.length, 3)
  assert(report.writes.every((r) => ['/api/v1/admin/users/member/policy', '/api/v1/admin/usage-pricing'].includes(r.path)))
  console.log(JSON.stringify(report))
} finally {
  await writeFile(resolve(reportDir, 'account-ui-report.json'), JSON.stringify(report, null, 2))
  await browser?.close(); await server.close()
}
