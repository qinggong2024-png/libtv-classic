import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_DOWNLOAD_NAME_REPORT_DIR, 'Set Playwright and an external report directory')
const output = resolve(process.env.UI_DOWNLOAD_NAME_REPORT_DIR)
assert(isAbsolute(relative(root, output)) || relative(root, output).startsWith('..'), 'Keep evidence outside the worktree')
await mkdir(output, { recursive: true })
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const frontendRequire = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(frontendRequire.resolve('vite')).href)
const mediaUrl = '/api/v1/cutflow-handoffs/multi/naming-fixture/outputs/render-1'
const result = { id: 'render-1', sha256: 'a'.repeat(64), reviewStatus: 'PENDING', durationSeconds: 1, width: 1080, height: 1920, createdAt: '2026-11-14T00:00:00Z', mediaUrl }
const task = { id: 'naming-fixture', status: 'PENDING_REVIEW', createdAt: result.createdAt, updatedAt: result.createdAt, sources: [{ clipId: 'clip-1', order: 1, taskId: 'task-1', variantId: 'variant-1', artifactId: 'artifact-1', title: 'TT 成片命名验收', durationSeconds: 2 }], outputs: [result] }
const html = `<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'></head><body><div id='root'></div><script type='module'>
import React from 'react';import{createRoot}from'react-dom/client';
import{TaskFinalDelivery}from'/src/features/production/TaskFinalDelivery.tsx';
import{CutflowBatchResult}from'/src/features/production/CutflowBatchResult.tsx';
import{CutflowControl}from'/src/features/production/CutflowControl.tsx';
import'/src/styles.css';
const task=${JSON.stringify(task)};
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
React.createElement('div',{id:'latest'},React.createElement(TaskFinalDelivery,{task,onPreview:()=>{}})),
React.createElement('div',{id:'history'},React.createElement(CutflowBatchResult,{task})),
React.createElement('div',{id:'single'},React.createElement(CutflowControl,{taskId:'task-1',variantId:'variant-1',artifactId:'artifact-1',siteProfileId:'tokyotiger',canWrite:false}))));</script></body></html>`
const report = { scope: 'isolated-fixture-no-production-writes', browser: null, downloads: [], errors: [], unexpectedRequests: [] }
const server = await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'), cacheDir: resolve(output, 'vite-cache'), logLevel: 'error', optimizeDeps: { entries: [] }, server: { host: '127.0.0.1', port: 0 }, plugins: [{ name: 'download-name-fixture', configureServer(vite) {
  // Native Chrome downloads can bypass Playwright routing: serve actual HTTP.
  vite.middlewares.use((req, res, next) => {
    const url = new URL(req.url, 'http://fixture')
    if (!url.pathname.startsWith('/api/')) return next()
    if (req.method !== 'GET') { report.unexpectedRequests.push(req.method + ' ' + req.url); res.statusCode = 405; return res.end() }
    res.setHeader('Cache-Control', 'private, no-store')
    if (url.pathname.includes('/outputs/')) {
      res.setHeader('Content-Type', 'video/mp4')
      if (url.searchParams.get('download') === 'true') res.setHeader('Content-Disposition', ['attachment; filename*=UTF-8', '', 'PMO-TT261114AA01.mp4'].join(String.fromCharCode(39)))
      return res.end(Buffer.from('isolated-edited-video-fixture'))
    }
    if (url.pathname === '/api/v1/cutflow-handoffs') {
      res.setHeader('Content-Type', 'application/json')
      return res.end(JSON.stringify({ enabled: true, siteRequired: false, handoffs: [{ id: 'single-naming', sourceArtifactId: 'artifact-1', status: 'PENDING_REVIEW', contractVersion: 'libtv-cut-lan/1', editMode: 'automatic', outputs: [{ ...result, mediaUrl: '/api/v1/cutflow-handoffs/single-naming/outputs/render-1' }] }] }))
    }
    report.unexpectedRequests.push(req.url); res.statusCode = 404; res.end()
  })
  vite.middlewares.use('/__download_names', async (_req, res, next) => { try { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(await vite.transformIndexHtml('/__download_names', html)) } catch (error) { next(error) } })
} }] })
await server.listen()
const origin = `http://127.0.0.1:${server.httpServer.address().port}`
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true, serviceWorkers: 'block' })
const page = await context.newPage()
report.browser = browser.version()
page.on('pageerror', error => report.errors.push(error.message))
try {
  await context.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin || request.method() !== 'GET') { report.unexpectedRequests.push(request.method() + ' ' + request.url()); return route.abort() }
    return route.continue()
  })
  await page.goto(origin + '/__download_names')
  const cases = [['latest', page.locator('#latest a[download]')], ['history', page.locator('#history a[download]')], ['single', page.locator('#single').getByRole('button', { name: /下载/ })]]
  for (const [name, locator] of cases) {
    const [download] = await Promise.all([page.waitForEvent('download'), locator.first().click()])
    assert.equal(download.suggestedFilename(), 'PMO-TT261114AA01.mp4')
    await download.saveAs(resolve(output, `${name}-${download.suggestedFilename()}`))
    report.downloads.push({ entry: name, filename: download.suggestedFilename() })
  }
  await page.screenshot({ path: resolve(output, 'desktop.png'), fullPage: true })
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.unexpectedRequests, [])
  console.log(JSON.stringify(report))
} finally {
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2))
  await context.close()
  await browser.close()
  await server.close()
}
