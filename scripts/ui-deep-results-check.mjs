import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_RESULTS_REPORT_DIR, 'Set UI_RESULTS_REPORT_DIR outside the worktrees')
const reportDir = resolve(process.env.UI_RESULTS_REPORT_DIR)
const reportRelative = relative(root, reportDir)
assert(reportRelative.startsWith('..') || isAbsolute(reportRelative), 'Use an external report directory')
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const videoPath = resolve(reportDir, 'source.mp4')
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=teal:s=240x320:d=5.2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', videoPath])
const video = await readFile(videoPath)
const image = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="320"><rect width="240" height="320" fill="#176d68"/><path d="M80 50h80l40 45-30 25-15-20v160H85V100l-15 20-30-25z" fill="#ece4d3"/></svg>')
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client'; import App from '/src/app/App.tsx'; import '/src/styles.css';
createRoot(document.getElementById('root')).render(React.createElement(App));</script></body></html>`
const server = await createServer({
  configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'results-fixture', configureServer(vite) {
    vite.middlewares.use('/__results_fixture', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__results_fixture', html)) }
      catch (error) { next(error) }
    })
  } }],
})
const report = { checks: [], errors: [], externalRequests: [], writes: [], originalReads: [] }
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const width of [1102, 390]) {
    for (const mode of ['recreate', 'depth']) {
      const context = await browser.newContext({ viewport: { width, height: 884 }, serviceWorkers: 'block', reducedMotion: 'reduce' })
      const page = await context.newPage()
      page.setDefaultTimeout(15000)
      page.on('pageerror', (error) => report.errors.push(error.message))
      const fixtures = createPageResponses({ role: 'USER' })
      const template = fixtures.get('/api/v1/tasks')[2]
      const source = { ...template.assets[0], id: 'original-video', role: 'REFERENCE_VIDEO', order: 0, originalFilename: 'reference.mp4' }
      const target = { ...template.assets[0], id: 'original-image', role: 'PRODUCT', order: 1, originalFilename: 'target.jpg' }
      const task = { ...template, id: 'deep-result', title: '深度视频成片', workflowKind: 'DEEP_VIDEO', userRequirement: '保持商品图案与自然动作 @Image 1', assets: mode === 'depth' ? [source] : [target, source], actionCapabilities: { ...template.actionCapabilities, canRegenerate: true }, settings: { deepVideoMode: mode, durationSeconds: 6, sourceDurationSeconds: 5.2, aspectRatio: '16:9' } }
      const originalReads = []
      let outputReads = 0
      await page.route('**/*', async (route) => {
        const request = route.request()
        const url = new URL(request.url())
        if (url.origin !== origin) { report.externalRequests.push(url.origin); return route.abort() }
        if (!url.pathname.startsWith('/api/')) return route.continue()
        if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
        if (url.pathname === '/api/v1/auth/me') return route.fulfill({ json: { ...fixtures.get(url.pathname), pagePermissions: ['make', 'records'] } })
        if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: [task] })
        if (url.pathname === '/api/v1/tasks/deep-result') return route.fulfill({ json: task })
        if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-16', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0, unpricedCount: 0, prices: {} } })
        if (url.pathname === '/api/v1/video-tasks/deep/status') return route.fulfill({ json: { configured: true, enabled: true, reason: null } })
        if (url.pathname === '/api/v1/library/kling/elements') return route.fulfill({ json: { items: [], pageNum: 1, pageSize: 100, total: 0, official: false } })
        if (url.pathname.includes('/cutflow')) return route.fulfill({ json: { enabled: false, handoffs: [] } })
        if (url.pathname === '/api/v1/fixture-media/product.png' || url.pathname.endsWith('/thumbnail')) return route.fulfill({ contentType: 'image/svg+xml', body: image })
        if (url.pathname === '/api/v1/downloads/synthetic-artifact') { outputReads++; return route.fulfill({ contentType: 'video/mp4', body: video }) }
        if (url.pathname.endsWith('/content')) {
          originalReads.push(url.pathname)
          return route.fulfill(url.pathname.includes('original-video') ? { contentType: 'video/mp4', body: video } : { contentType: 'image/svg+xml', body: image })
        }
        if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
        report.errors.push(`Unmocked API ${url.pathname}`)
        return route.fulfill({ status: 404, json: {} })
      })
      await page.goto(`${origin}/__results_fixture?view=records`)
      const card = page.locator('.records-completed .result-card')
      await card.waitFor()
      await card.locator('.result-cover img').waitFor()
      assert.equal(outputReads, 0, 'Posters must not download the full output')
      await card.locator('.result-cover').click()
      await page.waitForFunction(() => document.querySelector('[role="dialog"] video')?.readyState >= 2)
      assert.equal(outputReads, 1)
      assert.equal(new URL(page.url()).searchParams.get('task'), null, 'Cover plays without opening detail')
      await page.getByRole('button', { name: '关闭预览', exact: true }).click()
      await card.locator('.result-body > div').first().click()
      await page.locator('.production-workspace').waitFor()
      assert.equal(new URL(page.url()).searchParams.get('task'), task.id)
      const resultPreview = page.locator('.variant-result-preview')
      await resultPreview.locator('img').waitFor()
      await resultPreview.click()
      await page.waitForFunction(() => document.querySelector('[role="dialog"] video')?.readyState >= 2)
      await page.getByRole('button', { name: '关闭预览', exact: true }).click()
      await page.screenshot({ path: resolve(reportDir, `result-${mode}-${width}.png`), fullPage: true })
      await page.getByRole('button', { name: '使用相同素材再制作', exact: true }).click()
      const panel = page.getByRole('region', { name: '深度视频配置', exact: true })
      await panel.waitFor()
      await page.waitForFunction(() => document.querySelector('.video-reference-preview video')?.readyState >= 1)
      assert.equal(await page.getByRole('tab', { name: '视频复刻', exact: true }).getAttribute('aria-selected'), 'true')
      assert.equal(await panel.locator(`input[name="deep-mode"][value="${mode}"]`).isChecked(), true)
      assert.equal(await panel.locator('.video-reference-preview video').evaluate((el) => el.duration), 5.2)
      assert.equal(await panel.locator('.deep-video-images li').count(), mode === 'depth' ? 0 : 1)
      if (mode === 'recreate') {
        assert.equal(await panel.locator('select').inputValue(), '16:9')
        const prompt = panel.getByRole('combobox', { name: '复刻要求' })
        assert.equal(await prompt.inputValue(), task.userRequirement)
      }
      assert.deepEqual(originalReads.sort(), mode === 'depth' ? ['/api/v1/assets/original-video/content'] : ['/api/v1/assets/original-image/content', '/api/v1/assets/original-video/content'])
      await page.screenshot({ path: resolve(reportDir, `remix-${mode}-${width}.png`), fullPage: true })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      // The card's own remake button must not bubble into task details.
      await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '任务', exact: true }).click()
      await card.getByRole('button', { name: '同素材再制作', exact: true }).click()
      await panel.waitFor()
      assert.equal(new URL(page.url()).searchParams.get('task'), null)
      report.checks.push(`${width}px ${mode}: output poster/playback, body opens details, detail poster/playback, remake from detail and card, original video and ordered targets, mode/prompt/ratio preserved, no auto submission`)
      report.originalReads.push({ width, mode, paths: originalReads })
      await context.close()
    }
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
