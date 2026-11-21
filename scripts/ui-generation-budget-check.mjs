import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_BUDGET_REPORT_DIR, 'Set UI_BUDGET_REPORT_DIR outside the worktrees')
const reportDir = resolve(process.env.UI_BUDGET_REPORT_DIR)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client'; import App from '/src/app/App.tsx'; import '/src/styles.css';
createRoot(document.getElementById('root')).render(React.createElement(App));</script></body></html>`
const server = await createServer({ configFile: false, envFile: false, cacheDir: resolve(reportDir, 'vite-cache'), root: resolve(root, 'frontend'), server: { host: '127.0.0.1', port: 0 }, logLevel: 'error', plugins: [{ name: 'budget-fixture', configureServer(vite) {
  vite.middlewares.use('/__budget_fixture', async (_req, res, next) => {
    try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__budget_fixture', html)) }
    catch (error) { next(error) }
  })
}}] })
const report = { checks: [], errors: [], externalRequests: [], writes: [] }
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
  const page = await context.newPage()
  page.on('pageerror', (error) => report.errors.push(error.message))
  const rates = { imagePriceUsd: .04, videoPricePerSecondUsd: .25, depthVideoPricePerSecondUsd: .05, recreateVideoPricePerSecondUsd: .2 }
  let budget = { day: '2026-10-16', currency: 'USD', dailyBudgetUsd: 12, budgetUsedUsd: 2.38, unpricedCount: 0, prices: rates }
  let unavailable = false
  let budgetReads = 0
  let tasks = []
  await page.route('**/*', async (route) => {
    const request = route.request(); const url = new URL(request.url())
    if (url.origin !== origin && !['blob:', 'data:'].includes(url.protocol)) { report.externalRequests.push(url.origin); return route.abort() }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
    if (url.pathname === '/api/v1/account/generation-budget') { budgetReads++; return route.fulfill({ status: unavailable ? 503 : 200, json: unavailable ? { detail: 'Synthetic unavailable' } : budget }) }
    if (url.pathname === '/api/v1/system/status') return route.fulfill({ json: { environment: 'test', sharedAuthentication: true, videoGeneration: { provider: 'kling', enabled: true, available: true, authenticated: true, model: 'kling-v3-omni', models: ['kling-v3-omni'], imageInputLimit: 7, videoDurations: ['6', '12'] } } })
    if (url.pathname === '/api/v1/auth/me') return route.fulfill({ json: { userId: 'member', username: 'designer-a', role: 'USER', pagePermissions: ['images', 'make', 'records'] } })
    if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: tasks })
    if (url.pathname === '/api/v1/tasks/legacy') return route.fulfill({ json: tasks[0] })
    if (url.pathname === '/api/v1/prompt-presets') return route.fulfill({ json: [] })
    if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 20 } })
    if (url.pathname === '/api/v1/video-templates') return route.fulfill({ json: [{ id: 'template', status: 'active', name: '经典服装展示', minImages: 1, maxImages: 7 }] })
    if (url.pathname === '/api/v1/site-style-profiles') return route.fulfill({ json: [{ id: 'profile', status: 'active', name: '自然时装', tone: '自然、清晰的服装展示' }] })
    if (url.pathname.includes('/kling/elements')) return route.fulfill({ json: { items: [], total: 0 } })
    if (url.pathname === '/api/v1/video-tasks/deep/status') return route.fulfill({ json: { enabled: true, recreateModel: 'synthetic-video-edit' } })
    if (url.pathname === '/api/v1/account/usage') return route.fulfill({ json: { ...budget, total: 0, estimatedUsd: 0, reservedUsd: 2.38, uncertainUsd: 0, policies: [{ userId: 'member', username: 'designer-a', dailyBudgetUsd: 12 }], items: [], events: [] } })
    if (url.pathname === '/api/v1/auth/preferences/prompt-confirmation') return route.fulfill({ json: { automatic: false } })
    if (url.pathname === '/api/v1/auth/preferences/video-learning') return route.fulfill({ json: { enabled: false, feedbackCount: 0, scopeCount: 0 } })
    report.errors.push(`Unmocked API ${url.pathname}`)
    return route.fulfill({ status: 404, json: {} })
  })
  const refresh = () => page.evaluate(() => window.dispatchEvent(new Event('generation-budget-refresh')))
  const screenshot = async (name) => {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1100 })
      await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, behavior: 'instant' }) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name} ${width} overflow`)
      const summary = page.locator(width > 820 ? '.sidebar .daily-budget' : '.mobile-budget .daily-budget')
      await summary.waitFor({ state: 'visible' })
      const badge = page.locator('.generation-cost-badge:visible').last()
      if (await badge.count()) {
        const button = badge.locator('..')
        const box = await button.boundingBox(), label = await badge.boundingBox()
        assert(box && label && label.x >= box.x - 1 && label.x + label.width <= box.x + box.width + 1 && label.y + label.height <= box.y + box.height + 1, `${name} ${width} clipped cost label`)
      }
      await page.screenshot({ path: resolve(reportDir, `${name}-${width}.png`), fullPage: true })
      report.checks.push(`${name} ${width}: no overflow, budget and cost visible`)
    }
    await page.setViewportSize({ width: 1440, height: 1100 })
  }
  await page.goto(`${origin}/__budget_fixture?view=images`)
  await page.locator('.sidebar .daily-budget').getByText('$2.38', { exact: true }).waitFor()
  const png = { name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=', 'base64') }
  await page.locator('#image-center-model-files').setInputFiles(png)
  await page.locator('section[aria-label="产品图片"] input[type=file]').setInputFiles(png)
  const imageBadge = page.locator('.generation-cost-badge:visible').last()
  await imageBadge.getByText('预估 $0.04', { exact: true }).waitFor()
  await page.getByRole('group', { name: '统一结果数量' }).getByRole('button', { name: '4', exact: true }).click()
  await imageBadge.getByText('预估 $0.16', { exact: true }).waitFor()
  await screenshot('images')
  await page.locator('.sidebar').getByRole('button', { name: '视频制作', exact: true }).click()
  await page.locator('.composer input[type=file]').first().setInputFiles(png)
  await page.getByLabel('视频时长', { exact: true }).selectOption('12')
  await page.locator('.composer .generation-cost-badge').getByText('预估 $3.00', { exact: true }).waitFor()
  await page.locator('.composer-result-count label').filter({ hasText: /^4$/ }).click()
  await page.locator('.composer .generation-cost-badge').getByText('预估 $12.00', { exact: true }).waitFor()
  await page.locator('.composer input[type=file]').nth(1).setInputFiles(['sku-a_product.png', 'sku-a_s01.png', 'sku-b_product.png', 'sku-b_s01.png'].map((name) => ({ ...png, name })))
  await page.locator('.composer .generation-cost-badge').getByText('预估 $24.00', { exact: true }).waitFor()
  await screenshot('video')
  await page.getByRole('tab', { name: /深度视频|视频复刻/ }).click()
  const deep = page.locator('.video-recreation')
  await deep.locator('.generation-cost-badge').getByText('时长待确认', { exact: true }).waitFor()
  // A metadata event exercises the real panel without uploading a reference to any server.
  await page.getByLabel('参考视频', { exact: true }).setInputFiles({ name: 'reference.mp4', mimeType: 'video/mp4', buffer: Buffer.from('synthetic-metadata-fixture') })
  await page.locator('.video-reference-preview video').evaluate((video) => {
    Object.defineProperties(video, { duration: { value: 5.2 }, videoWidth: { value: 720 }, videoHeight: { value: 1280 } })
    video.dispatchEvent(new Event('loadedmetadata'))
  })
  await deep.locator('.generation-cost-badge').getByText('预估 $1.50', { exact: true }).waitFor()
  await deep.getByText('先提取深度视频 → 深度视频 + 目标参考图替换', { exact: true }).waitFor()
  await deep.getByText('Seedance 2.0 · WaveSpeed', { exact: true }).waitFor()
  await screenshot('depth-first-replacement')
  await page.getByRole('radio', { name: /仅提取深度视频/ }).check()
  await deep.locator('.generation-cost-badge').getByText('预估 $0.30', { exact: true }).waitFor()
  await screenshot('depth')
  budget = { ...budget, budgetUsedUsd: 3.58 }
  await refresh()
  await page.locator('.sidebar .daily-budget').getByText('$3.58', { exact: true }).waitFor()
  budget = { ...budget, prices: { ...rates, depthVideoPricePerSecondUsd: null }, unpricedCount: 1 }
  await page.clock.install()
  await page.clock.fastForward(15_000)
  await deep.locator('.generation-cost-badge').getByText('未定价', { exact: true }).waitFor()
  await page.locator('.sidebar .daily-budget').getByText('1 笔未定价 · 额度待核实', { exact: true }).waitFor()
  budget = { ...budget, day: '2026-10-17', budgetUsedUsd: 0, unpricedCount: 0, prices: { ...rates, depthVideoPricePerSecondUsd: 0 }, dailyBudgetUsd: null }
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await deep.locator('.generation-cost-badge').getByText('预估 $0.00', { exact: true }).waitFor()
  await page.locator('.sidebar .daily-budget').getByText('不限额', { exact: true }).waitFor()
  await page.locator('.sidebar .daily-budget').getByText('$0.00', { exact: true }).waitFor()
  unavailable = true
  await refresh()
  await deep.locator('.generation-cost-badge').getByText('费用暂不可用', { exact: true }).waitFor()
  await page.locator('.sidebar .daily-budget').getByText('额度读取失败，点击查看记录', { exact: true }).waitFor()
  unavailable = false
  await refresh()
  await deep.locator('.generation-cost-badge').getByText('预估 $0.00', { exact: true }).waitFor()
  await page.locator('.sidebar .daily-budget').click()
  await page.getByRole('heading', { name: '偏好设置', exact: true }).waitFor()
  tasks = [{ id: 'legacy', title: '经典视频方案', phase: 'AWAITING_CONFIRMATION', stateVersion: 1,
    createdAt: '2026-10-16T01:00:00Z', updatedAt: '2026-10-16T01:00:00Z', assets: [], assetCount: 0,
    analysisResult: null, generationProvider: 'kling', actionCapabilities: { canCancel: true, canRegenerate: true, canConfirm: true },
    variants: ['STABLE', 'DYNAMIC'].map((kind, index) => ({ id: kind, kind, revision: 1, promptText: '服装展示示例提示词', budgetDurationSeconds: index ? 12 : 6, generationStatus: 'NOT_STARTED', downloadStatus: 'PENDING', feedback: null })),
  }]
  await page.goto(`${origin}/__budget_fixture?view=make&task=legacy`)
  const confirmation = page.locator('.confirmation-submit')
  await confirmation.locator('.generation-cost-badge').getByText('预估 $4.50', { exact: true }).waitFor()
  await confirmation.locator('label').filter({ hasText: /^稳定展示$/ }).click()
  await confirmation.locator('.generation-cost-badge').getByText('预估 $1.50', { exact: true }).waitFor()
  tasks[0].phase = 'DELIVERABLE'
  await page.reload()
  const reuse = page.locator('.reuse-controls')
  await reuse.locator('.generation-cost-badge').getByText('预估 $4.50', { exact: true }).waitFor()
  await reuse.locator('label').filter({ hasText: /动态广告/ }).click()
  await reuse.locator('.generation-cost-badge').getByText('预估 $1.50', { exact: true }).waitFor()
  report.checks.push('Image count, video duration/results, deep mode, refresh, new day, unlimited, zero, unknown, error/recovery, usage navigation')
  report.checks.push('Confirmation and reuse sum only selected variants with their saved durations')
  assert(budgetReads >= 6)
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.externalRequests, [])
  assert.deepEqual(report.writes, [])
  await context.close()
} finally {
  await browser?.close()
  await server.close()
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
}
console.log(JSON.stringify(report))
