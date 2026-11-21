import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_EXISTING_DEPTH_REPORT_DIR, 'Set UI_EXISTING_DEPTH_REPORT_DIR outside the worktrees')
const reportDir = resolve(process.env.UI_EXISTING_DEPTH_REPORT_DIR)
const reportRelative = relative(root, reportDir)
assert(reportRelative.startsWith('..') || isAbsolute(reportRelative), 'Use an external report directory')
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const videoPath = resolve(reportDir, 'synthetic.mp4')
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=gray:s=240x320:d=5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', videoPath])
const video = await readFile(videoPath)
const image = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="720" height="960"><rect width="720" height="960" fill="#176d68"/><path d="M240 170h240l120 140-90 80-45-65v445H255V325l-45 65-90-80z" fill="#ece4d3"/></svg>')
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client'; import App from '/src/app/App.tsx'; import '/src/styles.css';
createRoot(document.getElementById('root')).render(React.createElement(App));</script></body></html>`
const server = await createServer({
  configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'preview-fixture', configureServer(vite) {
    vite.middlewares.use('/__preview_fixture', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__preview_fixture', html)) }
      catch (error) { next(error) }
    })
  } }],
})
const report = { checks: [], errors: [], externalRequests: [], writes: [] }
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const width of [1440, 1102, 820, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 884 }, serviceWorkers: 'block', reducedMotion: 'reduce' })
    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    page.on('pageerror', (error) => report.errors.push(error.message))
    const fixtures = createPageResponses({ role: 'USER' })
    const template = fixtures.get('/api/v1/tasks')[0]
    const reference = { ...template.assets[0], id: 'preview-video', originalFilename: 'reference.mp4', role: 'REFERENCE_VIDEO', order: 0 }
    const target = { ...template.assets[0], id: 'preview-image', originalFilename: 'target.jpg', role: 'PRODUCT', order: 1 }
    let task = { ...template, id: 'preview-task', title: '深度视频素材预览验收', workflowKind: 'DEEP_VIDEO', assets: [target, reference], assetCount: 2, phase: 'DELIVERABLE', generationProvider: 'wavespeed_video', settings: { deepVideoMode: 'recreate', deepVideoPipeline: 'depth-first-v1', deepVideoStage: 'replacement', durationSeconds: 8 }, depthVideoResult: { downloadId: 'saved-depth', completedAt: '2026-10-16T03:42:00Z' }, timing: { stages: [{ key: 'SOURCE_UPLOAD', status: 'COMPLETED', durationSeconds: 3 }, { key: 'DEPTH_EXTRACTION', status: 'COMPLETED', durationSeconds: 74 }, { key: 'REPLACEMENT', status: 'ACTIVE', activeStartedAt: new Date().toISOString(), durationSeconds: null }, { key: 'DOWNLOAD', status: 'PENDING' }] } }
    const failImage = false
    const contentReads = []
    await page.addInitScript(() => {
      window.previewUrls = new Set()
      const create = URL.createObjectURL.bind(URL)
      const revoke = URL.revokeObjectURL.bind(URL)
      URL.createObjectURL = (blob) => { const url = create(blob); window.previewUrls.add(url); return url }
      URL.revokeObjectURL = (url) => { window.previewUrls.delete(url); revoke(url) }
    })
    await page.route('**/*', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== origin) { report.externalRequests.push(url.origin); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (url.pathname === '/api/v1/uploads/video' && request.method() === 'POST') return route.fulfill({ status: 201, json: { id: 'uploaded-depth', durationSeconds: 5 } })
      if (url.pathname === '/api/v1/uploads' && request.method() === 'POST') return route.fulfill({ status: 201, json: { id: 'uploaded-image' } })
      if (url.pathname === '/api/v1/video-tasks/deep' && request.method() === 'POST') {
        const payload = request.postDataJSON(); report.writes.push(payload)
        assert.equal(payload.referenceVideoType, 'depth'); assert.equal(payload.mode, 'recreate')
        return route.fulfill({ status: 201, json: { ...task, id: 'created-direct', phase: 'QUEUED', settings: { deepVideoMode: 'recreate', deepVideoPipeline: 'depth-input-v1' } } })
      }
      if (request.method() !== 'GET') { report.errors.push(`Unexpected write ${url.pathname}`); return route.abort() }
      if (url.pathname === '/api/v1/auth/me') return route.fulfill({ json: { ...fixtures.get(url.pathname), pagePermissions: ['records', 'make', 'images'] } })
      if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: [task] })
      if (url.pathname === '/api/v1/tasks/preview-task') return route.fulfill({ json: task })
      if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], total: 0, limit: 20, offset: 0 } })
      if (url.pathname.includes('/kling/elements')) return route.fulfill({ json: { items: [], total: 0 } })
      if (url.pathname === '/api/v1/video-tasks/deep/status') return route.fulfill({ json: { enabled: true, recreateModel: 'seedance-2.0' } })
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { sites: [], defaultProfileId: null } })
      if (url.pathname === '/api/v1/cutflow-handoffs') return route.fulfill({ json: { enabled: false, handoffs: [] } })
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-16', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0, unpricedCount: 0, prices: { depthVideoPricePerSecondUsd: .05, recreateVideoPricePerSecondUsd: .2 } } })
      if (url.pathname === '/api/v1/fixture-media/product.png') return route.fulfill({ contentType: 'image/svg+xml', body: image })
      if (url.pathname === '/api/v1/downloads/saved-depth') return route.fulfill({ contentType: 'video/mp4', body: video })
      if (url.pathname === '/api/v1/downloads/saved-depth/thumbnail') return route.fulfill({ contentType: 'image/svg+xml', body: image })
      if (url.pathname.endsWith('/content')) {
        contentReads.push(url.pathname)
        if (url.pathname.includes('preview-video')) return route.fulfill({ contentType: 'video/mp4', body: video })
        if (failImage) return route.fulfill({ status: 404, json: { detail: 'asset file was not found' } })
        return route.fulfill({ contentType: 'image/svg+xml', body: image })
      }
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      report.errors.push(`Unmocked API ${url.pathname}`)
      return route.fulfill({ status: 404, json: {} })
    })
    await page.goto(`${origin}/__preview_fixture?task=preview-task`)
    await page.getByRole('button', { name: '用于替换', exact: true }).click()
    const panel = page.locator('.video-recreation:visible')
    await panel.getByRole('radio', { name: '已有深度视频', exact: true }).waitFor()
    assert.equal(await panel.getByRole('radio', { name: '已有深度视频', exact: true }).isChecked(), true)
    await panel.locator('video').waitFor()
    await page.waitForFunction(() => document.querySelector('.video-reference-preview video')?.readyState >= 2)
    await panel.locator('.generation-cost-badge').getByText('预估 $1.00', { exact: true }).waitFor()
    await panel.getByRole('radio', { name: '原始视频', exact: true }).check()
    assert.equal(await panel.locator('video').count(), 0)
    const dropzone = await panel.locator('.video-reference-upload').boundingBox()
    assert(dropzone && dropzone.height <= 300, `upload area too tall: ${dropzone?.height}`)
    const left = panel.locator('.video-reference-column')
    assert.equal(await left.getByLabel('输出画幅').count(), 1)
    assert.equal(await left.getByText('先提取深度，再替换人物与商品', { exact: true }).count(), 1)
    const footer = panel.getByRole('group', { name: '生成操作' })
    // The primary action must remain reachable while inspecting either column.
    const assertVisibleAction = async (control) => {
      await control.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }))
      const action = await footer.locator('button').boundingBox()
      const navigation = width <= 820 ? await page.locator('.mobile-nav').boundingBox() : null
      assert(action && action.y >= 0 && action.y + action.height <= (navigation?.y ?? 884), `action outside usable viewport at ${width}: ${JSON.stringify(action)}`)
      assert.equal(await footer.locator('button').evaluate((element) => {
        const box = element.getBoundingClientRect()
        return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.closest('.video-recreation-footer') === element.closest('.video-recreation-footer')
      }), true, 'action obscured by another surface')
      return action
    }
    await assertVisibleAction(panel.getByRole('button', { name: /拖拽或点击上传参考视频/ }))
    await page.screenshot({ path: resolve(reportDir, `balanced-source-${width}.png`) })
    await assertVisibleAction(panel.getByRole('combobox', { name: '复刻要求', exact: true }))
    const input = await panel.getByRole('combobox', { name: '复刻要求', exact: true }).boundingBox()
    const actionBar = await footer.boundingBox()
    assert(input && actionBar && input.y + input.height <= actionBar.y, 'sticky action covers prompt after scrolling into view')
    await page.screenshot({ path: resolve(reportDir, `balanced-prompt-${width}.png`) })
    if (width > 820) {
      const leftBox = await left.boundingBox()
      const rightBox = await panel.locator('.video-recreation-settings').boundingBox()
      assert(leftBox && rightBox && leftBox.x + leftBox.width <= rightBox.x, 'columns overlap')
      assert(Math.abs(leftBox.height - rightBox.height) <= 240, `unbalanced columns: ${leftBox.height} / ${rightBox.height}`)
    }
    // Both production modes retain a usable action bar.
    await panel.getByRole('radio', { name: /仅提取深度视频/ }).check()
    await assertVisibleAction(panel.getByRole('button', { name: /拖拽或点击上传参考视频/ }))
    await panel.getByRole('radio', { name: /人物与商品替换/ }).check()
    await page.screenshot({ path: resolve(reportDir, `compact-original-${width}.png`), fullPage: true })
    await panel.getByRole('radio', { name: '已有深度视频', exact: true }).check()
    await panel.getByRole('button', { name: '从历史结果选择', exact: true }).click()
    await page.getByRole('dialog', { name: '选择已有深度视频' }).waitFor()
    await page.locator('.depth-history-item').first().click()
    await page.waitForFunction(() => document.querySelector('.video-reference-preview video')?.readyState >= 2)
    await panel.locator('.generation-cost-badge').getByText('预估 $1.00', { exact: true }).waitFor()
    const png = { name: 'target.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=', 'base64') }
    await panel.locator('.deep-video-image-upload input[type=file]').setInputFiles(png)
    await panel.getByRole('button', { name: /开始替换/ }).waitFor({ state: 'visible' })
    assert.equal(await panel.getByRole('button', { name: /开始替换/ }).isEnabled(), true)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.screenshot({ path: resolve(reportDir, `existing-depth-${width}.png`), fullPage: true })
    await panel.getByRole('button', { name: /开始替换/ }).click()
    await page.waitForTimeout(300)
    assert.equal(report.writes.at(-1).referenceVideoType, 'depth')
    report.checks.push(`${width}px: result reuse, supplied-depth selected, compact upload height, switch clears prior video, history import, replacement-only price, mocked submit payload, balanced columns, sticky action without input overlap, extraction mode action, no overflow`)
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.externalRequests, [])
  assert.equal(report.writes.length, 4)
  console.log(JSON.stringify(report))
} finally {
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
  await browser?.close()
  await server.close()
}
