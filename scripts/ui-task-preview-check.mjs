import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_PREVIEW_REPORT_DIR, 'Set UI_PREVIEW_REPORT_DIR outside the worktrees')
const reportDir = resolve(process.env.UI_PREVIEW_REPORT_DIR)
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
    const task = { ...template, id: 'preview-task', title: '深度视频素材预览验收', workflowKind: 'DEEP_VIDEO', assets: [target, reference], assetCount: 2, settings: { deepVideoMode: 'depth', durationSeconds: 8 } }
    let failImage = false
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
    const imageButton = page.getByRole('button', { name: '预览目标参考图：target.jpg', exact: true })
    const videoButton = page.getByRole('button', { name: '预览参考视频：reference.mp4', exact: true })
    await imageButton.waitFor()
    await page.locator('.source-assets img').first().waitFor()
    assert.deepEqual(contentReads, [], 'Full files must load only on demand')
    assert.equal(await page.locator('.source-assets button').first().getAttribute('aria-label'), '预览参考视频：reference.mp4')
    const baselineUrls = await page.evaluate(() => window.previewUrls.size)
    await imageButton.focus()
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('img').waitFor()
    await page.waitForFunction(() => document.querySelector('[role="dialog"] img')?.naturalWidth === 720)
    await page.screenshot({ path: resolve(reportDir, `image-${width}.png`) })
    await page.keyboard.press('Tab')
    assert.equal(await page.getByRole('button', { name: '关闭素材预览' }).evaluate((el) => el === document.activeElement), true)
    await page.keyboard.press('Escape')
    await dialog.waitFor({ state: 'detached' })
    assert.equal(await imageButton.evaluate((el) => el === document.activeElement), true)
    assert.equal(await page.evaluate(() => window.previewUrls.size), baselineUrls)
    await videoButton.click()
    await page.waitForFunction(() => document.querySelector('[role="dialog"] video')?.readyState >= 2)
    assert.equal(await dialog.locator('video').evaluate((el) => el.controls && el.playsInline && el.duration === 2), true)
    await page.keyboard.press('Tab')
    assert.equal(await dialog.locator('video').evaluate((el) => el === document.activeElement), true)
    await dialog.locator('video').evaluate((el) => { el.pause(); el.currentTime = 1 })
    await page.waitForFunction(() => document.querySelector('[role="dialog"] video')?.currentTime >= 1)
    await page.screenshot({ path: resolve(reportDir, `video-${width}.png`) })
    await page.getByRole('button', { name: '关闭素材预览' }).click()
    await dialog.waitFor({ state: 'detached' })
    assert.equal(await page.locator('video').count(), 0)
    assert.equal(await page.evaluate(() => window.previewUrls.size), baselineUrls)
    failImage = true
    await imageButton.click()
    await dialog.getByRole('alert').waitFor()
    assert.equal(await dialog.locator('img').count(), 0)
    await page.locator('.modal-backdrop').click({ position: { x: 2, y: 2 } })
    await dialog.waitFor({ state: 'detached' })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    report.checks.push(`${width}px: task-center entry, source ordering, lazy full image, keyboard/focus, video playback/seeking, close cleanup, missing-media error, backdrop close, no overflow`)
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
