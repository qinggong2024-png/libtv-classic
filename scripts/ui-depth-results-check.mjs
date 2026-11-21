import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_DEPTH_REPORT_DIR, 'Set UI_DEPTH_REPORT_DIR outside the worktrees')
const reportDir = resolve(process.env.UI_DEPTH_REPORT_DIR)
const reportRelative = relative(root, reportDir)
assert(reportRelative.startsWith('..') || isAbsolute(reportRelative), 'Use an external report directory')
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const videoPath = resolve(reportDir, 'synthetic.mp4')
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=teal:s=240x320:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', videoPath])
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
  for (const width of [1102, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 884 }, serviceWorkers: 'block', reducedMotion: 'reduce' })
    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    page.on('pageerror', (error) => report.errors.push(error.message))
    const fixtures = createPageResponses({ role: 'USER' })
    const template = fixtures.get('/api/v1/tasks')[0]
    const reference = { ...template.assets[0], id: 'preview-video', originalFilename: 'reference.mp4', role: 'REFERENCE_VIDEO', order: 0 }
    const target = { ...template.assets[0], id: 'preview-image', originalFilename: 'target.jpg', role: 'PRODUCT', order: 1 }
    let task = { ...template, id: 'preview-task', title: '深度视频素材预览验收', workflowKind: 'DEEP_VIDEO', assets: [target, reference], assetCount: 2, phase: 'GENERATING', settings: { deepVideoMode: 'recreate', deepVideoPipeline: 'depth-first-v1', deepVideoStage: 'replacement', durationSeconds: 8 }, depthVideoResult: { downloadId: 'saved-depth', completedAt: '2026-10-16T03:42:00Z' }, timing: { stages: [{ key: 'SOURCE_UPLOAD', status: 'COMPLETED', durationSeconds: 3 }, { key: 'DEPTH_EXTRACTION', status: 'COMPLETED', durationSeconds: 74 }, { key: 'REPLACEMENT', status: 'ACTIVE', activeStartedAt: new Date().toISOString(), durationSeconds: null }, { key: 'DOWNLOAD', status: 'PENDING' }] } }
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
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
      if (url.pathname === '/api/v1/auth/me') return route.fulfill({ json: { ...fixtures.get(url.pathname), pagePermissions: ['records'] } })
      if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: [task] })
      if (url.pathname === '/api/v1/tasks/preview-task') return route.fulfill({ json: task })
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-16', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0, unpricedCount: 0, prices: {} } })
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
    await page.goto(`${origin}/__preview_fixture?view=records`)
    await page.locator('.task-table-row').click()
    await page.getByRole('region', { name: '深度视频结果' }).waitFor()
    assert.equal(await page.locator('.workspace-stages > div').count(), 4)
    assert.match(await page.locator('.workspace-stages [aria-current="step"]').innerText(), /Seedance 2.0 替换/)
    assert.equal(await page.locator('.task-timing-stage').count(), 4)
    assert.equal(await page.locator('.task-timing-stage.is-completed').count(), 2)
    assert.match(await page.locator('.task-timing-stage.is-active').innerText(), /人物.*替换/)
    assert.equal(await page.locator('video').count(), 0)
    await page.screenshot({ path: resolve(reportDir, `replacement-${width}.png`), fullPage: true })
    await page.getByRole('button', { name: '播放深度视频', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('[role="dialog"] video')?.readyState >= 2)
    await page.getByRole('button', { name: '关闭预览', exact: true }).click()
    const downloaded = page.waitForEvent('download')
    await page.getByRole('button', { name: '下载深度视频', exact: true }).click()
    assert.match((await downloaded).suggestedFilename(), /-depth.mp4$/)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    task = { ...task, phase: 'ACTION_REQUIRED', timing: { stages: task.timing.stages.map((stage) => stage.key === 'REPLACEMENT' ? { ...stage, status: 'FAILED', activeStartedAt: null, durationSeconds: 92 } : stage) } }
    await page.reload()
    await page.getByRole('button', { name: '下载深度视频', exact: true }).waitFor()
    assert.match(await page.locator('.task-timing-stage.is-failed').innerText(), /人物.*替换/)
    assert.equal(await page.locator('.task-timing-stage.is-active').count(), 0)
    await page.screenshot({ path: resolve(reportDir, `failed-${width}.png`), fullPage: true })
    task = { ...task, phase: 'GENERATING', depthVideoResult: null, settings: { ...task.settings, deepVideoStage: 'depth' }, timing: { stages: task.timing.stages.map((stage) => stage.key === 'DEPTH_EXTRACTION' ? { ...stage, status: 'ACTIVE', activeStartedAt: new Date().toISOString(), durationSeconds: null } : stage.key === 'REPLACEMENT' ? { ...stage, status: 'PENDING', durationSeconds: null } : stage) } }
    await page.reload()
    await page.locator('.task-timing-stage.is-active').waitFor()
    assert.match(await page.locator('.task-timing-stage.is-active').innerText(), /深度提取/)
    assert.equal(await page.getByRole('button', { name: '下载深度视频', exact: true }).count(), 0)
    await page.screenshot({ path: resolve(reportDir, `extraction-${width}.png`), fullPage: true })
    report.checks.push(`${width}px: extraction/replacement stages, separate timers, depth preview playback, download filename, failed replacement retains depth, no overflow`)
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
