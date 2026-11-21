import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_PROMPT_REPORT_DIR, 'Set UI_PROMPT_REPORT_DIR outside the worktrees')
const reportDir = resolve(process.env.UI_PROMPT_REPORT_DIR)
const reportRelative = relative(root, reportDir)
assert(reportRelative.startsWith('..') || isAbsolute(reportRelative), 'Use an external report directory')
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import {VideoRecreationPanel} from '/src/features/production/VideoRecreationPanel.tsx'; import '/src/styles.css';
const seed = location.search ? {prompt: '历史任务自定义要求', images: []} : undefined;
createRoot(document.getElementById('root')).render(React.createElement(VideoRecreationPanel, {actorId: 'fixture', active: true, seed, onCreated: () => {throw Error('Unexpected submission')}}));</script></body></html>`
const server = await createServer({
  configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'prompt-fixture', configureServer(vite) {
    vite.middlewares.use('/__prompt_fixture', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__prompt_fixture', html)) }
      catch (error) { next(error) }
    })
  } }],
})
const report = { checks: [], errors: [], writes: [], unexpected: [] }
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const width of [1102, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 884 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    page.on('pageerror', error => report.errors.push(error.message))
    await page.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== origin) { report.unexpected.push(url.origin); return route.abort() }
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
      if (url.pathname === '/api/v1/video-tasks/deep/status') return route.fulfill({ json: { configured: false, enabled: false, reason: '离线验证' } })
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { sites: [], defaultProfileId: null } })
      if (url.pathname.startsWith('/api/')) { report.unexpected.push(url.pathname); return route.abort() }
      return route.continue()
    })
    await page.goto(`${origin}/__prompt_fixture`)
    await page.locator('.generation-advanced > summary').click()
    const prompt = page.getByRole('combobox', { name: '复刻要求' })
    const value = await prompt.inputValue()
    assert.equal(value, '保持参考视频的镜头顺序、人物动作、运镜和节奏，用目标参考图中的人物与服装替换原主体。')
    assert(!value.includes('@Image'), 'Defaults must not reference missing uploads')
    const file = { name: 'background.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64') }
    await page.locator('input[type="file"][aria-label="背景参考图"]').setInputFiles(file)
    await page.getByRole('button', { name: '移除背景参考图' }).waitFor()
    assert.equal(await prompt.inputValue(), value)
    await page.getByRole('button', { name: '移除背景参考图' }).click()
    await page.getByRole('radio', { name: /深度提取与复刻/ }).check()
    assert.equal(await prompt.count(), 0, 'Depth extraction must not display recreation requirements')
    await page.getByRole('button', { name: /用深度视频复刻/ }).click()
    assert.equal(await prompt.inputValue(), value)
    await prompt.fill('保持衣服上的标志清晰')
    await page.getByRole('button', { name: '恢复推荐' }).click()
    assert.equal(await prompt.inputValue(), value)
    await prompt.scrollIntoViewIfNeeded()
    await page.screenshot({ path: resolve(reportDir, `default-${width}.png`), fullPage: true })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Horizontal overflow')
    await page.goto(`${origin}/__prompt_fixture?history=1`)
    await page.locator('.generation-advanced > summary').click()
    assert.equal(await prompt.inputValue(), '历史任务自定义要求')
    report.checks.push({ width, restoredDefault: true, backgroundToggle: true, depthInput: true, extractionWithoutRequirements: true, resetDefault: true, historyPreserved: true })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.writes, [])
  assert.deepEqual(report.unexpected, [])
} finally {
  await browser?.close()
  await server.close()
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
}
console.log(JSON.stringify(report))
