import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_PAGINATION_REPORT_DIR, 'Set UI_PAGINATION_REPORT_DIR outside the worktrees')
const reportDir = resolve(process.env.UI_PAGINATION_REPORT_DIR)
const reportRelative = relative(root, reportDir)
assert(reportRelative.startsWith('..') || isAbsolute(reportRelative), 'Use an external report directory')
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client'; import App from '/src/app/App.tsx'; import '/src/styles.css';
createRoot(document.getElementById('root')).render(React.createElement(App));</script></body></html>`
const server = await createServer({
  configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'pagination-fixture', configureServer(vite) {
    vite.middlewares.use('/__pagination_fixture', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__pagination_fixture', html)) }
      catch (error) { next(error) }
    })
  } }],
})
const report = { checks: [], cursors: [], errors: [], externalRequests: [], writes: [] }
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    page.on('pageerror', (error) => report.errors.push(error.message))
    const fixtures = createPageResponses({ role: 'USER' })
    const actor = { ...fixtures.get('/api/v1/auth/me'), pagePermissions: ['records'] }
    const template = fixtures.get('/api/v1/tasks')[0]
    let owner = actor.userId
    let headReads = 0
    const cursors = []
    const task = (index) => ({
      ...template, id: `${owner}-task-${index}`, userId: owner, title: `分页回归任务 ${index}`,
      phase: 'DRAFT', phaseReasonCode: null, variants: [],
      ...(index === 0 ? { title: `首页刷新 ${headReads}`, stateVersion: headReads } : {}),
    })
    await page.route('**/*', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== origin) { report.externalRequests.push(url.origin); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
      if (url.pathname === '/api/v1/auth/me') return route.fulfill({ json: { ...actor, userId: owner } })
      if (url.pathname === '/api/v1/tasks') {
        const cursor = url.searchParams.get('cursor')
        cursors.push(cursor)
        if (cursor === null) headReads++
        const index = cursor === null ? 0 : cursor === 'older-1' ? 1 : cursor === 'older-2' ? 2 : -1
        assert(index >= 0, `Unexpected cursor ${cursor}`)
        return route.fulfill({ json: [task(index)], headers: index < 2 ? { 'X-Next-Cursor': `older-${index + 1}` } : {} })
      }
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: {
        day: '2026-10-16', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0,
        unpricedCount: 0, prices: {},
      } })
      if (url.pathname === '/api/v1/fixture-media/product.png') return route.fulfill({
        contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64'),
      })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      report.errors.push(`Unmocked API ${url.pathname}`)
      return route.fulfill({ status: 404, json: {} })
    })
    await page.clock.install()
    await page.goto(`${origin}/__pagination_fixture?view=records`)
    await page.getByText('首页刷新 1', { exact: true }).waitFor()
    const loadMore = page.getByRole('button', { name: '加载更早记录', exact: true })
    await loadMore.click()
    await page.getByText('分页回归任务 1', { exact: true }).waitFor()
    // Exercise the actual automatic poll between two history loads.
    await page.clock.runFor(31_000)
    await page.getByText('首页刷新 2', { exact: true }).waitFor()
    await loadMore.click()
    await page.getByText('分页回归任务 2', { exact: true }).waitFor()
    assert.deepEqual(cursors.slice(0, 4), [null, 'older-1', null, 'older-2'])
    assert.equal(await page.locator('.task-table-row').count(), 3)
    assert.equal(await loadMore.count(), 0)
    await page.clock.runFor(31_000)
    await page.getByText('首页刷新 3', { exact: true }).waitFor()
    assert.equal(await loadMore.count(), 0, 'Polling must preserve exhausted history')
    assert.equal(await page.locator('.task-table-row').count(), 3)
    await page.screenshot({ path: resolve(reportDir, `records-${width}.png`), fullPage: true })
    // A changed account must start with its own records and pagination cursor.
    owner = 'synthetic-second-account'
    await page.clock.runFor(31_000)
    await page.getByText('首页刷新 4', { exact: true }).waitFor()
    assert.equal(await page.locator('.task-table-row').count(), 1)
    await loadMore.waitFor()
    await loadMore.click()
    await page.getByText('分页回归任务 1', { exact: true }).waitFor()
    assert.equal(cursors.at(-1), 'older-1')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    report.checks.push(`${width}px: poll preserves history cursor and exhaustion; new account resets history`)
    report.cursors.push({ width, cursors })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.externalRequests, [])
  assert.deepEqual(report.writes, [])
  console.log(JSON.stringify(report))
} finally {
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
  await browser?.close()
  await server.close()
}
