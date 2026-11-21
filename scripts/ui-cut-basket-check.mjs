import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { createServer as createPortProbe } from 'node:net'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_BASKET_REPORT_DIR, 'Set PLAYWRIGHT_MODULE and UI_BASKET_REPORT_DIR')
const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const reportDir = resolve(process.env.UI_BASKET_REPORT_DIR)
await mkdir(reportDir, { recursive: true })
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
let server
let targetUrl = process.env.UI_BASKET_BASE_URL
if (!targetUrl) {
  const { createServer } = await import(pathToFileURL(createRequire(resolve(root, 'frontend/package.json')).resolve('vite')).href)
  const html = '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">import React from "react"; import {createRoot} from "react-dom/client"; import App from "/src/app/App.tsx"; import "/src/styles.css"; createRoot(document.getElementById("root")).render(React.createElement(App));</script></body></html>'
  const probe = createPortProbe()
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve))
  const port = probe.address().port
  await new Promise(resolve => probe.close(resolve))
  server = await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'),
    cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error', server: { host: '127.0.0.1', port, strictPort: true },
    plugins: [{ name: 'cut-basket-fixture', configureServer(vite) {
      vite.middlewares.use('/__basket_fixture', async (_req, res, next) => {
        try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__basket_fixture', html)) }
        catch (error) { next(error) }
      })
    } }],
  })
  await server.listen()
  targetUrl = 'http://127.0.0.1:' + server.httpServer.address().port + '/__basket_fixture'
}
const origin = new URL(targetUrl).origin
const entryUrl = new URL(targetUrl)
entryUrl.searchParams.set('view', 'records')
const fixtures = createPageResponses()
const first = fixtures.get('/api/v1/tasks').find(task => task.phase === 'DELIVERABLE')
first.title = '跨任务混剪 · 针织衫'
first.siteProfileId = 'synthetic-style'
first.settings.autoSubmitToCutflow = false
const second = structuredClone(first)
second.id = 'synthetic-task-second'
second.title = '跨任务混剪 · 卫衣'
second.variants[0].id = 'synthetic-variant-second'
second.variants[0].downloadArtifactId = 'synthetic-artifact-second'
const tasks = [first, second]
const actor = fixtures.get('/api/v1/auth/me')
const storageKey = 'cutflow-multi-source-selection:' + actor.userId
const initialSelection = tasks.map(task => task.variants[0].id)
const report = { scope: 'isolated-fixture-no-production-writes', cases: [], errors: [], writes: [], externalRequests: [] }
const browser = await chromium.launch({ channel: 'chrome', headless: true })
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage()
    page.on('pageerror', error => report.errors.push(error.message))
    await context.addInitScript(({ storageKey, initialSelection }) => {
      sessionStorage.setItem(storageKey, JSON.stringify(initialSelection))
    }, { storageKey, initialSelection })
    await context.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== origin) { report.externalRequests.push(url.href); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.fulfill({ status: 403, json: {} }) }
      if (url.pathname === '/api/v1/system/status') return route.fulfill({ json: { ...fixtures.get(url.pathname), cutflowMultiSourceReady: true } })
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-11-13', currency: 'USD', dailyBudgetUsd: 50, budgetUsedUsd: 0, unpricedCount: 0, prices: { cutflowPriceUsd: 0.15 } } })
      if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: tasks })
      const task = tasks.find(item => url.pathname === '/api/v1/tasks/' + item.id)
      if (task) return route.fulfill({ json: task })
      if (url.pathname === '/api/v1/cutflow-handoffs') return route.fulfill({ json: { enabled: true, handoffs: [] } })
      if (url.pathname === '/api/v1/cutflow-handoffs/multi') return route.fulfill({ json: { tasks: [] } })
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { defaultProfileId: 'synthetic-style', sites: [{ id: 'synthetic-style', name: '合成品牌', version: 'test' }] } })
      if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], hasMore: false, nextOffset: 0 } })
      if (url.pathname.endsWith('/thumbnail') || url.pathname.includes('fixture-media')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="240"><rect width="160" height="240" fill="#d8e4e7"/><path d="M50 40L25 70L40 95L55 85V190H110V85L125 95L140 70L115 40Z" fill="#486979"/></svg>' })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      return route.fulfill({ status: 404, json: { detail: 'Isolated fixture: no live API calls' } })
    })
    await page.goto(entryUrl.href)
    await page.getByRole('heading', { name: '任务中心', exact: true }).waitFor()
    await page.locator('.cut-basket-compact').waitFor()
    assert.equal(await page.locator('[aria-label="剪辑篮"]').count(), 1)
    assert.equal(await page.locator('.cut-basket').count(), 0)
    await page.screenshot({ path: resolve(reportDir, 'compact-' + width + '.png') })
    await page.getByRole('button', { name: '展开送剪篮，已选 2 条原片', exact: true }).click()
    assert.equal(await page.locator('.cut-basket-compact').count(), 0)
    assert.equal(await page.locator('.cut-basket').count(), 1)
    const box = await page.locator('.cut-basket').boundingBox()
    assert(box && box.x >= 0 && box.x + box.width <= width + 1 && box.height <= 451)
    await page.locator('.cut-basket__frame').first().focus()
    await page.keyboard.press('ArrowRight')
    assert.deepEqual(await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), storageKey), [...initialSelection].reverse())
    await page.screenshot({ path: resolve(reportDir, 'expanded-' + width + '.png') })
    await page.getByRole('button', { name: '图片中心', exact: true }).click()
    await page.getByRole('heading', { name: '图片中心', exact: true }).waitFor()
    assert.equal(await page.locator('[aria-label="剪辑篮"]').count(), 0)
    assert.equal(await page.locator('.cut-basket, .cut-basket-compact').count(), 0)
    await page.screenshot({ path: resolve(reportDir, 'images-' + width + '.png') })
    await page.getByRole('button', { name: '任务', exact: true }).click()
    await page.getByRole('heading', { name: '任务中心', exact: true }).waitFor()
    await page.locator('.cut-basket-compact').waitFor()
    assert.deepEqual(await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), storageKey), [...initialSelection].reverse())
    await page.getByRole('button', { name: '展开送剪篮，已选 2 条原片', exact: true }).click()
    await page.locator('.task-center-card__main').first().click()
    await page.locator('.workspace-view').waitFor()
    await page.locator('.cut-basket-compact').waitFor()
    assert.equal(await page.locator('.cut-basket').count(), 0)
    assert.equal(await page.locator('[aria-label="剪辑篮"]').count(), 1)
    await page.getByRole('button', { name: '视频制作', exact: true }).click()
    await page.locator('.home-view:not([hidden])').waitFor()
    assert.equal(await page.locator('[aria-label="剪辑篮"]').count(), 0)
    report.cases.push({ width, compact: true, expandable: true, keyboardOrdering: true, imagesHidden: true, creationHidden: true, selectionPreserved: true, detailCollapsed: true })
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
