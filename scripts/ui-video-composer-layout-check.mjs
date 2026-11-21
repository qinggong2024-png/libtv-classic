import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_VIDEO_LAYOUT_REPORT_DIR, 'Set an external UI_VIDEO_LAYOUT_REPORT_DIR')
const reportDir = resolve(process.env.UI_VIDEO_LAYOUT_REPORT_DIR)
for (const tree of [root, resolve(root, '../libtv-classic-0e3d1c8-main')]) {
  const path = relative(tree, reportDir)
  assert(path.startsWith('..') || isAbsolute(path), 'Keep reports outside both worktrees')
}
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const fixtures = createPageResponses()
const system = fixtures.get('/api/v1/system/status')
system.videoGeneration.available = true
system.videoGeneration.authenticated = true
const budget = { prices: { videoPricePerSecondUsd: 0.15 } }
await mkdir(reportDir, { recursive: true })
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React, {useState} from 'react'; import {createRoot} from 'react-dom/client';
import {ProductionComposer} from '/src/features/production/ProductionComposer.tsx';
import {GenerationBudgetContext} from '/src/generationBudget.ts'; import '/src/styles.css';
function Fixture() {
  const [system,setSystem]=useState(${JSON.stringify(system)});
  window.disableFixtureProvider=()=>setSystem(previous=>({...previous,videoGeneration:{...previous.videoGeneration,available:false}}));
  return React.createElement(GenerationBudgetContext.Provider,{value:{budget:${JSON.stringify(budget)},failed:false}},
    React.createElement('div',{className:'app-shell'},
      React.createElement('aside',{className:'sidebar'},React.createElement('strong',null,'衣影工坊')),
      React.createElement('div',{className:'app-body'},React.createElement('main',{className:'home-view'},
        React.createElement('h1',null,'视频制作'),
        React.createElement(ProductionComposer,{seed:null,system,actorId:'layout-fixture',onCreated:()=>{throw Error('Unexpected submission')}})))));
}
createRoot(document.getElementById('root')).render(React.createElement(Fixture));
</script></body></html>`
const server = await createServer({
  configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'video-layout-fixture', configureServer(vite) {
    vite.middlewares.use('/__video_layout', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__video_layout', html)) }
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
  for (const width of [1920, 1440, 1066, 821, 768, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: 912 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    page.on('pageerror', error => report.errors.push(error.message))
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== origin) { report.unexpected.push(url.origin); return route.abort() }
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { sites: [{ id: 'synthetic-style', name: '合成品牌', version: 'fixture-v1', siteUrl: 'https://fixture.example/' }], defaultProfileId: 'synthetic-style' } })
      if (url.pathname === '/api/v1/library/kling/elements') return route.fulfill({ json: { items: [], pageNum: 1, pageSize: 100, total: 0 } })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      if (url.pathname.startsWith('/api/')) { report.unexpected.push(url.pathname); return route.abort() }
      return route.continue()
    })
    await page.goto(`${origin}/__video_layout`)
    const composer = page.getByRole('region', { name: '视频制作配置' })
    const button = composer.getByRole('button', { name: /开始生成/ })
    await composer.getByText('请先上传视频画面', { exact: true }).waitFor()
    assert(await button.isDisabled(), `${width}: empty form cannot submit`)
    async function check(state) {
      await composer.evaluate(element => window.scrollTo({ top: scrollY + element.getBoundingClientRect().bottom - innerHeight, behavior: 'instant' }))
      const bounds = await composer.evaluate(element => {
        const rect = selector => {
          const node = element.querySelector(selector), box = node.getBoundingClientRect()
          return { top: box.top, bottom: box.bottom, left: box.left, right: box.right, width: box.width, height: box.height }
        }
        const row = element.querySelector('.composer-submit-row')
        return {
          advanced: rect('.generation-advanced'), area: rect('.composer-submit-area'), row: rect('.composer-submit-row'),
          button: rect('.composer-submit-row .primary-button'), summary: rect('.composer-submit-summary'),
          hint: rect('.composer-submit-summary small'), feedback: rect('.composer-feedback'),
          alert: element.querySelector('.composer-feedback .inline-alert') ? rect('.composer-feedback .inline-alert') : null,
          rowPosition: getComputedStyle(row).position,
          areaPosition: getComputedStyle(element.querySelector('.composer-submit-area')).position,
          overflow: document.documentElement.scrollWidth > innerWidth + 1,
        }
      })
      assert(bounds.area.top - bounds.advanced.bottom >= 16, `${state}/${width}: separate settings and submission`)
      assert(bounds.row.top - bounds.area.top >= 16, `${state}/${width}: submission top padding`)
      assert(bounds.hint.height > 0, `${state}/${width}: payment consent stays visible`)
      assert.equal(bounds.rowPosition, 'relative', `${state}/${width}: action stays inside the sticky submission area`)
      assert.equal(bounds.areaPosition, 'sticky', `${state}/${width}: entire submission area sticks together`)
      assert(!bounds.overflow, `${state}/${width}: no horizontal overflow`)
      for (const part of [bounds.row, bounds.button, bounds.summary, bounds.alert].filter(Boolean)) {
        assert(part.left >= bounds.area.left && part.right <= bounds.area.right + 1, `${state}/${width}: content fits footer`)
      }
      if (bounds.alert) assert(bounds.alert.top - bounds.row.bottom >= 10, `${state}/${width}: alert separated from action`)
      if (width <= 820) assert(bounds.button.top - bounds.summary.bottom >= 12, `${state}/${width}: mobile stack spacing`)
      else assert(bounds.button.left - bounds.summary.right >= 24, `${state}/${width}: desktop action separation`)
      await composer.screenshot({ path: resolve(reportDir, `${state}-${width}.png`) })
      report.checks.push({ width, state, bounds })
    }
    async function checkSticky(state) {
      await composer.evaluate(element => {
        element.querySelector('.composer-inputs--video').style.minHeight = `${innerHeight * 2}px`
        const tail = document.createElement('div')
        tail.id = 'scroll-test-tail'
        tail.style.height = '200vh'
        document.body.append(tail)
      })
      for (const offset of [0, 360]) {
        await composer.evaluate((element, offset) => window.scrollTo({ top: scrollY + element.getBoundingClientRect().top + offset, behavior: 'instant' }), offset)
        const bounds = await composer.evaluate(element => {
          const area = element.querySelector('.composer-submit-area').getBoundingClientRect()
          const button = element.querySelector('.composer-submit-row .primary-button').getBoundingClientRect()
          return { top: area.top, bottom: area.bottom, buttonTop: button.top, buttonBottom: button.bottom, viewportHeight: innerHeight }
        })
        assert(Math.abs(bounds.bottom - bounds.viewportHeight) <= 1, `${state}/${width}/${offset}: footer sticks to viewport bottom`)
        assert(bounds.buttonTop >= 0 && bounds.buttonBottom <= bounds.viewportHeight, `${state}/${width}/${offset}: generate button stays visible`)
        report.checks.push({ width, state: `${state}-sticky-${offset}`, bounds })
        if (offset === 360) await page.screenshot({ path: resolve(reportDir, `${state}-sticky-${width}.png`) })
      }
      await composer.evaluate(element => window.scrollTo({ top: scrollY + element.getBoundingClientRect().bottom + 40, behavior: 'instant' }))
      assert(await composer.locator('.composer-submit-area').evaluate(element => element.getBoundingClientRect().bottom < 0), `${state}/${width}: footer releases after leaving the composer`)
      await composer.evaluate(element => {
        element.querySelector('.composer-inputs--video').style.removeProperty('min-height')
        document.getElementById('scroll-test-tail').remove()
      })
    }
    await check('empty')
    await checkSticky('empty')
    const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
    await composer.locator('.upload-panel--storyboard input[type=file]').setInputFiles({ name: 'frame.png', mimeType: 'image/png', buffer: pixel })
    await page.waitForFunction(() => !document.querySelector('.composer-submit-row button').disabled)
    await check('ready')
    await composer.locator('.generation-advanced > summary').click()
    await composer.getByLabel('本条展示重点', { exact: true }).selectOption('print')
    const productAnalysis = composer.getByRole('checkbox', { name: /让 Sol 看商品参考图/ })
    assert(await productAnalysis.isChecked(), `${width}: detail focus enables product evidence`)
    assert(await productAnalysis.isDisabled(), `${width}: focus explains automatic product evidence`)
    assert(!(await button.isDisabled()), `${width}: missing product detail adds no generation gate`)
    await composer.getByLabel('人物动作幅度', { exact: true }).selectOption('high')
    await composer.getByLabel('本条展示重点', { exact: true }).selectOption('overall')
    assert(!(await productAnalysis.isChecked()), `${width}: whole-outfit focus stays lightweight`)
    assert(!(await productAnalysis.isDisabled()), `${width}: optional product evidence remains available`)
    await composer.getByLabel('本条展示重点', { exact: true }).selectOption('print')
    await composer.locator('.composer-result-count label').filter({ hasText: /^4$/ }).click()
    await composer.getByText('预计 4 条视频', { exact: false }).waitFor()
    await check('advanced')
    await checkSticky('advanced')
    await composer.locator('.generation-advanced > summary').click()
    await page.evaluate(() => window.disableFixtureProvider())
    await composer.getByText(/当前不可用。请让管理员/).waitFor()
    assert(await button.isDisabled(), `${width}: unavailable provider cannot submit`)
    await check('unavailable')
    await checkSticky('unavailable')
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
