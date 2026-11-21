import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { createServer as createPortProbe } from 'node:net'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_UPLOAD_REPORT_DIR, 'Set PLAYWRIGHT_MODULE and UI_UPLOAD_REPORT_DIR')
const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const reportDir = resolve(process.env.UI_UPLOAD_REPORT_DIR)
await mkdir(reportDir, { recursive: true })
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const { createServer } = await import(pathToFileURL(createRequire(resolve(root, 'frontend/package.json')).resolve('vite')).href)
const fixtures = createPageResponses()
const system = fixtures.get('/api/v1/system/status')
system.videoGeneration = { ...system.videoGeneration, available: true, authenticated: true, supportsElementReferences: false, supportsProductReferences: true }
const html = '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">'
  + 'import React from "react"; import {createRoot} from "react-dom/client"; import {ProductionComposer} from "/src/features/production/ProductionComposer.tsx"; import "/src/styles.css";'
  + 'const canvas=document.createElement("canvas");canvas.width=256;canvas.height=256;const ctx=canvas.getContext("2d");ctx.fillStyle="#d8e4e7";ctx.fillRect(0,0,256,256);ctx.fillStyle="#486979";ctx.fillRect(75,40,110,180);'
  + 'const data=Uint8Array.from(atob(canvas.toDataURL("image/png").split(",")[1]),c=>c.charCodeAt(0));const file=name=>new File([data],name,{type:"image/png"});'
  + 'const seed={id:"fixture",storyboards:[file("分镜01.png"),file("分镜02.png")],products:[file("商品参考01.png")],requirement:"合成验收"};'
  + 'createRoot(document.getElementById("root")).render(React.createElement(ProductionComposer,{seed,system:' + JSON.stringify(system) + ',actorId:"fixture",onCreated:()=>{throw Error("Unexpected task creation")}}));</script></body></html>'
const probe = createPortProbe()
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve))
const port = probe.address().port
await new Promise(resolve => probe.close(resolve))
const server = await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error', server: { host: '127.0.0.1', port, strictPort: true },
  plugins: [{ name: 'upload-diagnostics-fixture', configureServer(vite) {
    vite.middlewares.use('/__upload_fixture', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__upload_fixture', html)) }
      catch (error) { next(error) }
    })
  } }],
})
await server.listen()
const origin = 'http://127.0.0.1:' + port
const report = { scope: 'isolated-fixture-no-production-writes', cases: [], errors: [], unexpectedWrites: [], externalRequests: [] }
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const cases = [
  { name: 'dimensions', status: 422, code: 'IMAGE_DIMENSIONS_INVALID', message: '图片尺寸为 100 × 640 像素，宽、高均须在 128～8192 像素之间。', expected: '100 × 640' },
  { name: 'internal', status: 500, code: 'UPLOAD_PROCESSING_FAILED', message: '网站处理上传文件时发生内部错误，尚不能认定图片不合格，请联系管理员。', expected: '尚不能认定图片不合格' },
  { name: 'request-field', status: 422, code: 'REQUEST_VALIDATION_FAILED', message: 'request validation failed', fieldErrors: [{ loc: ['header', 'Idempotency-Key'], type: 'missing', msg: 'Field required' }], expected: '请填写上传请求凭证' },
]
try {
  for (const width of [1440, 390]) for (const test of cases) {
    let releaseSuccess, releaseFailure
    const successGate = new Promise(resolve => { releaseSuccess = resolve })
    const failureGate = new Promise(resolve => { releaseFailure = resolve })
    const uploads = []
    const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage()
    page.on('pageerror', error => report.errors.push(error.message))
    await context.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== origin) { report.externalRequests.push(url.href); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (url.pathname === '/api/v1/uploads' && request.method() === 'POST') {
        const filename = request.postDataBuffer().toString('utf8').match(/filename="([^"]+)"/)?.[1]
        uploads.push(filename)
        if (filename === '商品参考01.png') {
          await failureGate
          return route.fulfill({ status: test.status, json: { detail: { code: test.code, message: test.message, correlationId: 'fixture-support-42', ...(test.fieldErrors ? { fieldErrors: test.fieldErrors } : {}) } } })
        }
        await successGate
        return route.fulfill({ status: 201, json: { id: filename, originalFilename: filename, width: 256, height: 256, sha256: 'fixture', mimeType: 'image/jpeg', sizeBytes: 1024, thumbnailUrl: '' } })
      }
      if (request.method() !== 'GET') { report.unexpectedWrites.push(url.pathname); return route.fulfill({ status: 403, json: {} }) }
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { defaultProfileId: 'synthetic-style', sites: [{ id: 'synthetic-style', name: '合成品牌', version: 'test' }] } })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      return route.fulfill({ status: 404, json: {} })
    })
    try {
      await page.goto(origin + '/__upload_fixture')
      await page.locator('.upload-validation-status--preview').nth(2).waitFor()
      assert.equal(await page.locator('.upload-validation-status--preview').count(), 3)
      await page.waitForFunction(() => [...document.querySelectorAll('img[src^="blob:"]')].filter(image => image.complete && image.naturalWidth > 0).length === 3)
      const submit = page.locator('.generation-priced-button')
      await submit.click()
      await page.locator('.upload-validation-status--uploading').nth(2).waitFor()
      assert.equal(await page.locator('.upload-validation-status--validated').count(), 0)
      releaseSuccess()
      await page.locator('.upload-validation-status--validated').nth(1).waitFor()
      assert.equal(await page.locator('.upload-validation-status--uploading').count(), 1)
      releaseFailure()
      await page.locator('.upload-validation-status--failed').waitFor()
      const body = await page.locator('body').innerText()
      assert(body.includes('商品参考图 1“商品参考01.png”'))
      assert(body.includes(test.expected), body)
      assert(body.includes('排查编号：fixture-support-42'))
      assert(!body.includes('提交结果未知'))
      assert.equal(await submit.isDisabled(), false)
      assert.equal(uploads.length, 3)
      const dimensions = await page.evaluate(() => ({ viewport: innerWidth, scroll: document.documentElement.scrollWidth }))
      assert(dimensions.scroll <= dimensions.viewport + 1, JSON.stringify(dimensions))
      const clippedStatuses = await page.locator('.upload-validation-status').evaluateAll(statuses => statuses.flatMap(status => {
        const rect = status.getBoundingClientRect()
        const card = status.closest('.material-card, .subject-reference-preview').getBoundingClientRect()
        const container = status.closest('.materials-grid, .subject-reference-previews').getBoundingClientRect()
        return rect.bottom > Math.min(card.bottom, container.bottom) + 1 || status.scrollWidth > status.clientWidth + 1 || status.scrollHeight > status.clientHeight + 1
          ? [status.textContent] : []
      }))
      assert.deepEqual(clippedStatuses, [])
      await page.screenshot({ path: resolve(reportDir, test.name + '-' + width + '.png'), fullPage: true })
      report.cases.push({ width, error: test.code, localPreview: true, uploading: true, validated: 2, failed: 1, specificFileAndRule: true, noTaskSubmitted: true, noOverflow: true })
    } finally { releaseSuccess(); releaseFailure(); await context.close() }
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.unexpectedWrites, [])
  assert.deepEqual(report.externalRequests, [])
  console.log(JSON.stringify(report))
} finally {
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
  await browser.close()
  await server.close()
}
