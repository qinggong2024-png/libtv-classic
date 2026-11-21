import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.UI_BRAND_ALIAS_REPORT_DIR, 'Set UI_BRAND_ALIAS_REPORT_DIR outside the worktrees')
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
const reportDir = resolve(process.env.UI_BRAND_ALIAS_REPORT_DIR)
await mkdir(reportDir, { recursive: true })
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const frontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(frontend.resolve('vite')).href)
const html = `<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body><main id="root"></main><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import {UserManagement} from '/src/features/admin/UserManagement.tsx'; import '/src/styles.css';
createRoot(document.getElementById('root')).render(React.createElement(UserManagement,{adminId:'root',sharedAuthentication:true}));
</script></body></html>`
const server = await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'), cacheDir: resolve(reportDir, 'cache'), logLevel: 'error', server: { host: '127.0.0.1', port: 0 }, plugins: [{ name: 'brand-alias-fixture', configureServer(vite) {
  vite.middlewares.use('/__brand_fixture', async (_req, res, next) => {
    try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__brand_fixture', html)) }
    catch (error) { next(error) }
  })
} }] })
const brands = [
  ['NW', 'NW / NerdyWave'], ['PW', 'PW / PageWings'], ['IP', 'IP / IronPandaFit'],
  ['TT', 'TT / Tokyo-Tiger'], ['dark', '暗黑 / NW / NerdyWave'],
  ['GuidingCross', 'GC / GuidingCross'], ['ClassyOutlaw', 'CLO / ClassyOutlaw'],
  ['TheRetroTee', 'TRT / TheRetroTee'], ['OrionRide', 'OR / OrionRide'],
  ['BoldOversize', 'BO / BoldOversize'],
].map(([id, displayName]) => ({ id, name: id === 'dark' ? '暗黑' : id, displayName }))
const report = { checks: [], errors: [], unexpectedRequests: [], submissions: [] }
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ headless: true })
  for (const width of [1440, 726, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 884 } })
    const page = await context.newPage()
    const users = [{ id: 'root', username: 'classic-admin', role: 'ADMIN', isActive: true, brandId: null, createdAt: '2026-10-21T01:00:00Z' }]
    page.on('pageerror', error => report.errors.push(error.message))
    await page.route('**/*', async route => {
      const request = route.request(); const url = new URL(request.url())
      if (url.origin !== origin) { report.unexpectedRequests.push(request.url()); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (url.pathname === '/api/v1/admin/brands' && request.method() === 'GET') return route.fulfill({ json: brands })
      if (url.pathname === '/api/v1/admin/users') {
        if (request.method() === 'GET') return route.fulfill({ json: users })
        if (request.method() === 'POST') {
          const { username, brandId, role, isBrandAdmin } = request.postDataJSON()
          assert.equal(brandId, 'TT')
          const created = { id: 'member', username, brandId, role, isBrandAdmin, isActive: true, createdAt: '2026-10-21T02:00:00Z', pagePermissions: ['images', 'make', 'records'] }
          users.push(created); report.submissions.push({ width, username, brandId })
          return route.fulfill({ status: 201, json: created })
        }
      }
      if (url.pathname === '/api/v1/admin/usage-pricing' && request.method() === 'GET') return route.fulfill({ json: { version: 1, imagePriceUsd: 0, videoPricePerSecondUsd: 0, depthVideoPricePerSecondUsd: 0, recreateVideoPricePerSecondUsd: 0, legacyConflicts: [] } })
      if (url.pathname === '/api/v1/account/usage' && request.method() === 'GET') return route.fulfill({ json: { day: '2026-10-21', currency: 'USD', total: 0, budgetUsedUsd: 0, estimatedUsd: 0, reservedUsd: 0, uncertainUsd: 0, unpricedCount: 0, policies: [], items: [], events: [] } })
      report.unexpectedRequests.push(`${request.method()} ${url.pathname}`)
      return route.fulfill({ status: 404, json: {} })
    })
    await page.goto(`${origin}/__brand_fixture`)
    await page.getByRole('button', { name: '创建账号', exact: true }).click()
    const select = page.locator('.account-create-form').getByLabel('所属品牌')
    await select.locator('option[value="TT"]').waitFor({ state: 'attached' })
    assert.deepEqual(await select.locator('option').allTextContents(), ['未分配（个人隔离）', ...brands.map(b => b.displayName)])
    assert.equal(await select.inputValue(), '')
    await page.getByLabel('登录账号').fill('synthetic-member')
    await page.getByLabel('临时密码').fill('synthetic-test-password')
    await select.selectOption('TT')
    await select.scrollIntoViewIfNeeded()
    await page.screenshot({ path: resolve(reportDir, `brand-dropdown-${width}.png`) })
    await page.getByRole('dialog', { name: '创建账号', exact: true }).getByRole('button', { name: '创建账号', exact: true }).click()
    await page.getByText('账号“synthetic-member”已创建，可以使用临时密码登录。', { exact: true }).waitFor()
    const member = page.locator('.account-row').filter({ hasText: 'synthetic-member' })
    assert.match(await member.innerText(), /TT \/ Tokyo-Tiger/)
    await member.getByRole('button', { name: '权限与预算' }).click()
    const policy = page.getByRole('form', { name: 'synthetic-member 的权限与预算' })
    assert.equal(await policy.getByLabel('所属品牌').inputValue(), 'TT')
    assert.deepEqual(await policy.getByLabel('所属品牌').locator('option').allTextContents(), ['未分配（个人隔离）', ...brands.map(b => b.displayName)])
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false)
    report.checks.push({ width, options: brands.length, createdBrand: 'TT', policyBrand: 'TT', overflow: false })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.unexpectedRequests, [])
  assert.equal(report.submissions.length, 3)
  console.log(JSON.stringify(report))
} finally {
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
  await browser?.close(); await server.close()
}
