import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_RECREATION_REPORT_DIR, 'Set an external UI_RECREATION_REPORT_DIR')
const reportDir = resolve(process.env.UI_RECREATION_REPORT_DIR)
for (const tree of [root, resolve(root, '../libtv-classic-0e3d1c8-main')]) {
  const path = relative(tree, reportDir)
  assert(path.startsWith('..') || isAbsolute(path), 'Keep reports outside both worktrees')
}
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import {VideoRecreationPanel} from '/src/features/production/VideoRecreationPanel.tsx'; import '/src/styles.css';
createRoot(document.getElementById('root')).render(React.createElement('div',{className:'app-shell'},
  React.createElement('aside',{className:'sidebar'},React.createElement('strong',null,'衣影工坊')),
  React.createElement('div',{className:'app-body'},React.createElement('main',null,
    React.createElement(VideoRecreationPanel,{actorId:'recreation-layout-fixture',active:true,onCreated:()=>{throw Error('Unexpected submission')}})))));
</script></body></html>`
const server = await createServer({
  configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'recreation-layout-fixture', configureServer(vite) {
    vite.middlewares.use('/__recreation_layout', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__recreation_layout', html)) }
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
  for (const width of [1440, 1066, 821, 768, 390]) {
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
    await page.goto(`${origin}/__recreation_layout`)
    await page.getByRole('heading', { name: '上传参考视频，换成你的人物和商品' }).waitFor()
    async function check(mode) {
      const bounds = await page.locator('.video-recreation-grid').evaluate(grid => {
        const [left, right] = [...grid.children].map(child => child.getBoundingClientRect())
        const upload = grid.querySelector('.video-reference-upload').getBoundingClientRect()
        const rightContent = grid.querySelector('.recreation-explainer, .deep-video-image-dropzone').getBoundingClientRect()
        const history = grid.parentElement.querySelector('.video-source-help')?.getBoundingClientRect()
        return { left: { top: left.top, bottom: left.bottom, width: left.width }, right: { top: right.top, bottom: right.bottom, width: right.width }, uploadTop: upload.top, uploadBottom: upload.bottom, historyTop: history?.top, contentTop: rightContent.top, uploadHeight: upload.height, contentHeight: rightContent.height }
      })
      if (width > 820) {
        assert(Math.abs(bounds.left.top - bounds.right.top) < 1, `${mode}/${width}: tops`)
        if (mode === 'depth') {
          assert(bounds.contentHeight < bounds.uploadHeight * 0.85, `${mode}/${width}: supporting explanation stays smaller`)
        } else {
          assert(Math.abs(bounds.left.bottom - bounds.right.bottom) < 1, `${mode}/${width}: bottoms`)
          assert(Math.abs(bounds.uploadBottom - bounds.right.bottom) < 1, `${mode}/${width}: upload bottom matches settings`)
        }
        assert(Math.abs(bounds.left.width - bounds.right.width) < 1, `${mode}/${width}: widths`)
        assert(Math.abs(bounds.uploadTop - bounds.contentTop) < 1, `${mode}/${width}: upload tops`)
      } else {
        assert(bounds.right.top >= bounds.left.bottom, `${mode}/${width}: stacked`)
      }
      if (mode === 'depth-recreate' || mode === 'with-images') {
        assert(bounds.historyTop >= Math.max(bounds.left.bottom, bounds.right.bottom), `${mode}/${width}: history below aligned content`)
      }
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${mode}/${width}: horizontal overflow`)
      await page.locator('.video-recreation').screenshot({ path: resolve(reportDir, `${mode}-${width}.png`) })
      report.checks.push({ width, mode, bounds })
    }
    await check('quick')
    const brief = page.getByRole('combobox', { name: '补充要求（可选）' })
    await brief.fill('使用第一张图的人物和第二张图的衣服。')
    assert((await brief.boundingBox()).height <= 100, 'Brief starts compact')
    await page.getByRole('radio', { name: '深度提取与复刻' }).check()
    await check('depth')
    await page.getByRole('button', { name: '用深度视频复刻' }).click()
    await check('depth-recreate')
    assert.equal(await brief.inputValue(), '使用第一张图的人物和第二张图的衣服。')
    const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
    await page.getByLabel('目标参考图', { exact: true }).setInputFiles(
      Array.from({ length: 5 }, (_, i) => ({ name: `reference-${i}.png`, mimeType: 'image/png', buffer: pixel })))
    await page.getByRole('button', { name: '移除参考图 5', exact: true }).waitFor()
    await check('with-images')
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
console.log(JSON.stringify({ checks: report.checks.length, errors: report.errors, writes: report.writes, unexpected: report.unexpected }))
