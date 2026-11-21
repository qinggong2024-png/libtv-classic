import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const reportDir = resolve(process.env.UI_VIDEO_REMIX_REPORT_DIR ?? '')
const reportRelative = relative(root, reportDir)
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_VIDEO_REMIX_REPORT_DIR, 'Set UI_VIDEO_REMIX_REPORT_DIR')
assert(reportRelative.startsWith('..') || isAbsolute(reportRelative), 'Use an external report directory')

const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const fixtures = createPageResponses({ role: 'USER' })
const baseTask = fixtures.get('/api/v1/tasks')[2]
const task = {
  ...baseTask,
  id: 'video-remix-source',
  title: '同素材再制作参考图验收',
  workflowKind: 'FIXED_TEMPLATE',
  actionCapabilities: { ...baseTask.actionCapabilities, canRegenerate: true },
  assets: [
    ['storyboard-01.png', 'STORYBOARD', 1],
    ['storyboard-02.png', 'STORYBOARD', 2],
    ['product-front.png', 'PRODUCT', 101],
    ['product-back.png', 'PRODUCT', 102],
    ['product-side.png', 'PRODUCT', 103],
  ].map(([name, role, order]) => ({
    ...baseTask.assets[0],
    id: `asset-${name}`,
    role,
    order,
    originalFilename: name,
    thumbnailUrl: `/api/v1/fixture-media/${name}`,
    usedForGeneration: true,
  })),
}

const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'
import { createRoot } from 'react-dom/client'
import App from '/src/app/App.tsx'
import '/src/styles.css'
createRoot(document.getElementById('root')).render(React.createElement(App))
</script></body></html>`

const server = await createServer({
  configFile: false,
  envFile: false,
  root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'),
  logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'video-remix-materials-fixture', configureServer(vite) {
    vite.middlewares.use('/__video_remix_materials', async (_request, response, next) => {
      try {
        response.setHeader('Content-Type', 'text/html')
        response.end(await vite.transformIndexHtml('/__video_remix_materials', html))
      } catch (error) {
        next(error)
      }
    })
  } }],
})

await mkdir(reportDir, { recursive: true })
const report = { checks: [], pageErrors: [], externalRequests: [], writes: [], unmatchedApiRequests: [] }
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const width of [1100, 390]) {
    let releaseHeldAsset
    const heldAsset = new Promise(resolveAsset => { releaseHeldAsset = resolveAsset })
    let storyboard02Reads = 0
    const context = await browser.newContext({
      viewport: { width, height: 900 },
      serviceWorkers: 'block',
      reducedMotion: 'reduce',
    })
    const page = await context.newPage()
    page.on('pageerror', error => report.pageErrors.push(error.message))
    await page.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== origin) {
        report.externalRequests.push(url.origin)
        return route.abort()
      }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (request.method() !== 'GET') {
        report.writes.push(`${request.method()} ${url.pathname}`)
        return route.abort()
      }
      if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: [task] })
      if (url.pathname === `/api/v1/tasks/${task.id}`) return route.fulfill({ json: task })
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-29', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0, unpricedCount: 0, prices: {} } })
      if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], hasMore: false, nextOffset: 0 } })
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { defaultProfileId: null, sites: [] } })
      if (url.pathname === '/api/v1/cutflow-handoffs/multi') return route.fulfill({ json: { tasks: [] } })
      if (url.pathname === '/api/v1/library/kling/elements' || url.pathname === '/api/v1/admin/kling/elements') {
        return route.fulfill({ json: { items: [], pageNum: 1, pageSize: 100, total: 0, official: false } })
      }
      if (url.pathname.startsWith('/api/v1/fixture-media/')) {
        if (url.pathname.endsWith('/storyboard-02.png')) {
          storyboard02Reads += 1
          if (storyboard02Reads === 2) await heldAsset
        }
        return route.fulfill({ contentType: 'image/png', body: png })
      }
      if (url.pathname.endsWith('/thumbnail')) return route.fulfill({ contentType: 'image/png', body: png })
      if (url.pathname.includes('/cutflow')) return route.fulfill({ json: { enabled: false, handoffs: [] } })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      report.unmatchedApiRequests.push(url.pathname)
      return route.fulfill({ status: 404, json: {} })
    })

    await page.goto(`${origin}/__video_remix_materials?view=records`)
    const card = page.locator('.records-completed .result-card')
    await card.waitFor()
    await card.locator('.result-body > div').first().click()
    await page.locator('.production-workspace').waitFor()
    await page.getByRole('button', { name: '使用相同素材再制作', exact: true }).click()
    await page.waitForFunction(() => new URLSearchParams(location.search).get('task') === null)
    await page.waitForFunction(() => {
      const home = document.querySelector('.home-view')
      return home instanceof HTMLElement && !home.hidden
    })
    await page.locator('.global-loading').waitFor()
    assert.match(await page.locator('.global-loading').innerText(), /正在加载原素材/)
    assert.equal(await page.evaluate(() => window.scrollY), 0)
    releaseHeldAsset()
    await page.locator('.materials-grid .material-card').first().waitFor()
    assert.equal(await page.locator('.materials-grid .material-card').count(), 2)
    assert.equal(await page.locator('.subject-reference-preview').count(), 3)
    assert.deepEqual(
      await page.locator('.subject-reference-preview__image').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label'))),
      [
        '主参考图预览：product-front.png',
        '补充参考图 1预览：product-back.png',
        '补充参考图 2预览：product-side.png',
      ],
    )
    assert.equal(await page.evaluate(() => window.scrollY), 0)
    assert.equal(await page.getByRole('button', { name: '清除主体', exact: true }).isVisible(), true)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
    await page.screenshot({
      path: resolve(reportDir, `video-remix-materials-${width}.png`),
      fullPage: true,
    })
    report.checks.push({
      width,
      storyboardCount: 2,
      productReferenceCount: 3,
      orderedReferences: ['product-front.png', 'product-back.png', 'product-side.png'],
      navigatedBeforeAssetsLoaded: true,
      pageTop: true,
      noHorizontalOverflow: true,
    })
    await context.close()
  }
  assert.deepEqual(report.pageErrors, [])
  assert.deepEqual(report.externalRequests, [])
  assert.deepEqual(report.writes, [])
  assert.deepEqual(report.unmatchedApiRequests, [])
  await writeFile(resolve(reportDir, 'video-remix-materials-report.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report))
} finally {
  await browser?.close()
  await server.close()
}
