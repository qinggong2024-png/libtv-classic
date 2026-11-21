import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'
import { startClassicServer } from './classic_web_server.mjs'

assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_AUTOCUT_REPORT_DIR, 'Set PLAYWRIGHT_MODULE and UI_AUTOCUT_REPORT_DIR')
assert(process.env.UI_AUTOCUT_BASE_URL || process.env.UI_AUTOCUT_DIST, 'Select a deployed URL or a built static directory')
const root = fileURLToPath(new URL('..', import.meta.url))
const reportDir = resolve(process.env.UI_AUTOCUT_REPORT_DIR)
await mkdir(reportDir, { recursive: true })
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
let server
let targetUrl = process.env.UI_AUTOCUT_BASE_URL
if (!targetUrl) {
  server = await startClassicServer({ root: resolve(root, process.env.UI_AUTOCUT_DIST), hosts: ['127.0.0.1'], port: 0, apiPort: 1 })
  targetUrl = 'http://127.0.0.1:' + server.port + '/'
}
const entry = new URL(targetUrl)
entry.searchParams.set('view', 'make')
const fixtures = createPageResponses()
const report = { target: entry.href, scope: 'real-static-assets-with-isolated-api-fixtures', cases: [], assets: [], errors: [], writes: [], externalRequests: [] }
const browser = await chromium.launch({ channel: 'chrome', headless: true })
try {
  for (const width of [1280, 1920, 3840]) {
    const context = await browser.newContext({ viewport: { width, height: width === 3840 ? 1800 : 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage()
    page.on('pageerror', error => report.errors.push(error.message))
    const assetReads = []
    page.on('response', response => {
      if (new URL(response.url()).pathname.startsWith('/assets/index-') && ['.js', '.css'].some(ext => new URL(response.url()).pathname.endsWith(ext))) {
        assetReads.push(response.body().then(body => ({ path: new URL(response.url()).pathname, sha256: createHash('sha256').update(body).digest('hex') })))
      }
    })
    await context.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== entry.origin) { report.externalRequests.push(url.href); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.fulfill({ status: 403, json: {} }) }
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-11-13', currency: 'USD', dailyBudgetUsd: 30, budgetUsedUsd: 0, unpricedCount: 0, prices: {} } })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      return route.fulfill({ status: 404, json: { detail: 'Isolated UI fixture' } })
    })
    await page.goto(entry.href)
    await page.locator('.home-view:not([hidden]) .composer[aria-label="视频制作配置"]').waitFor()
    const settings = page.locator('.home-view:not([hidden]) .composer[aria-label="视频制作配置"] .generation-advanced')
    const automatic = settings.locator('.composer-output-autocut input[type="checkbox"]')
    await automatic.waitFor({ state: 'attached' })
    assert.equal(await settings.count(), 1)
    assert.equal(await settings.getAttribute('open'), null)
    assert.equal(await automatic.isVisible(), false)
    assert.equal(await page.locator('.composer-routing-options .composer-auto-cutflow').count(), 0)
    assert.equal(await page.locator('.composer-output-autocut').count(), 1)
    await page.screenshot({ path: resolve(reportDir, 'collapsed-' + width + '.png'), fullPage: true })
    await settings.locator(':scope > summary').click()
    await automatic.waitFor({ state: 'visible' })
    assert.equal(await automatic.isChecked(), true)
    const label = settings.locator('.composer-output-autocut')
    const bodyBox = await settings.locator('.generation-advanced__fields').boundingBox()
    const labelBox = await label.boundingBox()
    assert(bodyBox && labelBox && Math.abs(bodyBox.x - labelBox.x) <= 2, 'Auto-edit option must align with advanced settings')
    assert(labelBox.x >= 0 && labelBox.x + labelBox.width <= width + 1)
    await automatic.uncheck()
    assert.match(await page.locator('.composer-submit-summary').innerText(), /仅生成原片/)
    assert.equal(await settings.locator('.generation-advanced__state.is-customized').count(), 1)
    await settings.getByRole('button', { name: '恢复推荐', exact: true }).click()
    assert.equal(await automatic.isChecked(), true)
    assert.match(await page.locator('.composer-submit-summary').innerText(), /完成后自动送剪/)
    await automatic.focus()
    assert(await automatic.evaluate(input => document.activeElement === input))
    await page.screenshot({ path: resolve(reportDir, 'expanded-' + width + '.png'), fullPage: true })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    report.assets.push(...await Promise.all(assetReads))
    report.cases.push({ width, collapsedByDefault: true, noMiddleCard: true, alignedInAdvancedSettings: true, defaultEnabled: true, toggleAndReset: true, keyboardFocus: true })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.writes, [])
  assert.deepEqual(report.externalRequests, [])
  console.log(JSON.stringify(report))
} finally {
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close()
  await server?.close()
}
