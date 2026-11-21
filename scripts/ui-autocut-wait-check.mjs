import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'
import { startClassicServer } from './classic_web_server.mjs'

assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_AUTOCUT_WAIT_REPORT_DIR && process.env.UI_AUTOCUT_WAIT_DIST,
  'Set PLAYWRIGHT_MODULE, UI_AUTOCUT_WAIT_REPORT_DIR and UI_AUTOCUT_WAIT_DIST')
const root = fileURLToPath(new URL('..', import.meta.url))
const reportDir = resolve(process.env.UI_AUTOCUT_WAIT_REPORT_DIR)
await mkdir(reportDir, { recursive: true })
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const server = await startClassicServer({ root: resolve(root, process.env.UI_AUTOCUT_WAIT_DIST), hosts: ['127.0.0.1'], port: 0, apiPort: 1 })
const origin = 'http://127.0.0.1:' + server.port
const fixtures = createPageResponses()
const readyAt = Date.parse('2026-11-13T06:23:36Z')
const task = structuredClone(fixtures.get('/api/v1/tasks').find(item => item.phase === 'DELIVERABLE'))
task.title = '自动送剪等待验收'
task.updatedAt = new Date(readyAt).toISOString()
task.settings.autoSubmitToCutflow = true
task.variants[0].downloadStatus = 'READY'
task.variants[0].validationStatus = 'PASSED'
const report = { scope: 'built-static-desktop-ui-with-isolated-read-only-api-fixtures', cases: [], errors: [], writes: [], externalRequests: [] }
const browser = await chromium.launch({ channel: 'chrome', headless: true })
try {
  for (const width of [1440, 1920]) {
    if (width === 1920) task.variants.push({ ...task.variants[0], id: 'synthetic-variant-second', downloadArtifactId: 'synthetic-artifact-second' })
    let readCount = 0
    let batches = []
    let readError = false
    const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage()
    page.setDefaultTimeout(12_000)
    page.on('pageerror', error => report.errors.push(error.message))
    await page.clock.install({ time: new Date(readyAt + 10_000) })
    await context.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== origin) { report.externalRequests.push(url.href); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.fulfill({ status: 403, json: {} }) }
      if (url.pathname === '/api/v1/system/status') return route.fulfill({ json: { ...fixtures.get(url.pathname), cutflowMultiSourceReady: true } })
      if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: [task] })
      if (url.pathname === '/api/v1/tasks/' + task.id) return route.fulfill({ json: task })
      if (url.pathname === '/api/v1/cutflow-handoffs') return route.fulfill({ json: { enabled: true, handoffs: [] } })
      if (url.pathname === '/api/v1/cutflow-handoffs/multi') {
        readCount++
        return route.fulfill(readError ? { status: 503, json: { detail: '隔离测试：暂时无法读取剪辑记录' } } : { json: { tasks: batches } })
      }
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-11-13', currency: 'USD', dailyBudgetUsd: 30, budgetUsedUsd: 0, unpricedCount: 0, prices: {} } })
      if (url.pathname.endsWith('/thumbnail') || url.pathname.includes('fixture-media')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="180"><rect width="120" height="180" fill="#d8e4e7"/></svg>' })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      return route.fulfill({ status: 404, json: { detail: 'Isolated fixture; no live API calls' } })
    })
    await page.goto(origin + '/?view=records')
    await page.getByRole('button', { name: '查看任务详情：' + task.title }).click()
    const status = page.locator('[aria-label="当前剪辑状态"]')
    await status.getByRole('heading', { name: '原片已就绪，等待自动送剪', exact: true }).waitFor()
    assert.equal(await status.getAttribute('role'), 'status')
    assert.equal(await status.locator('.spin').count(), 1)
    assert.equal(await status.locator('.lucide-triangle-alert').count(), 0)
    assert.equal(await page.locator('.workspace-view .task-direct-cut-button').count(), 0)
    assert.equal(await page.locator('.workspace-view .cutflow-source-toggle').count(), 0)
    assert.equal(await page.locator('.workspace-view .task-original-gallery__sidebar').count(), 0)
    const initialReads = readCount
    await page.clock.runFor(5_000)
    await page.waitForFunction(() => true)
    assert(readCount > initialReads, 'The waiting window should automatically query again within five seconds')
    await page.screenshot({ path: resolve(reportDir, 'waiting-' + width + '.png') })

    await page.clock.setFixedTime(new Date(readyAt + 89_000))
    await status.getByRole('button', { name: '刷新状态', exact: true }).click()
    await status.getByRole('heading', { name: '原片已就绪，等待自动送剪', exact: true }).waitFor()
    await page.clock.setFixedTime(new Date(readyAt + 90_000))
    await status.getByRole('button', { name: '刷新状态', exact: true }).click()
    await status.getByRole('heading', { name: '自动送剪等待较久', exact: true }).waitFor()
    // Refreshing task data can briefly remount the detail while the history
    // request completes. Assert the settled message, not that transient frame.
    await page.waitForFunction(() => {
      const notice = document.querySelector('[aria-label="当前剪辑状态"][role="alert"]')
      return notice?.textContent.includes('系统会继续查询') && !notice.textContent.includes('送剪失败')
    })
    await page.screenshot({ path: resolve(reportDir, 'overdue-' + width + '.png') })

    readError = true
    await status.getByRole('button', { name: '刷新状态', exact: true }).click()
    await status.getByRole('heading', { name: '暂时无法查询剪辑状态', exact: true }).waitFor()
    await page.screenshot({ path: resolve(reportDir, 'query-error-' + width + '.png') })

    readError = false
    batches = [{ id: 'synthetic-auto-cut', status: 'IMPORTING', createdAt: task.updatedAt, updatedAt: task.updatedAt,
      sources: [{ taskId: task.id, variantId: task.variants[0].id, artifactId: task.variants[0].downloadArtifactId, title: task.title }],
      outputs: [], errorCode: null, errorMessage: null }]
    await status.getByRole('button', { name: '重新查询', exact: true }).click()
    await page.waitForFunction(() => !document.querySelector('.workspace-view')?.textContent.includes('自动送剪等待较久')
      && document.querySelector('.workspace-view')?.textContent.includes('剪辑'))
    assert.equal(await page.locator('.workspace-view').getByRole('heading', { name: '原片已就绪，等待自动送剪', exact: true }).count(), 0)
    await page.screenshot({ path: resolve(reportDir, 'accepted-' + width + '.png') })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    const box = await page.locator('.production-workspace').boundingBox()
    assert(box && box.x >= 0 && box.x + box.width <= width + 1)
    report.cases.push({ width, neutralWaiting: true, automaticRefresh: true, timeoutAt90Seconds: true,
      failedQueryDistinct: true, acceptedRecordWins: true, noDuplicateSubmitEntry: true, noHorizontalOverflow: true })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.writes, [])
  assert.deepEqual(report.externalRequests, [])
  console.log(JSON.stringify(report))
} finally {
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close()
  await server.close()
}
