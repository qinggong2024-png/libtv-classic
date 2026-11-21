import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_IMAGE_BATCH_DESIGN_REPORT_DIR, 'Set an external UI_IMAGE_BATCH_DESIGN_REPORT_DIR')
const reportDir = resolve(process.env.UI_IMAGE_BATCH_DESIGN_REPORT_DIR)
for (const tree of [root, resolve(root, '../libtv-classic-0e3d1c8-main')]) {
  const path = relative(tree, reportDir)
  assert(path.startsWith('..') || isAbsolute(path), 'Keep reports outside both worktrees')
}
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer, preview } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import {ImageCenter} from '/src/features/image-center/ImageCenter.tsx';
import {loadImageCenterDraft} from '/src/imageCenterStore.ts';
import '/src/styles.css';
const scope='image-layout-fixture';
window.readDraft=()=>loadImageCenterDraft(scope);
createRoot(document.getElementById('root')).render(React.createElement('div',{className:'app-shell'},
  React.createElement('aside',{className:'sidebar'},React.createElement('strong',null,'衣影工坊')),
  React.createElement('div',{className:'app-body'},React.createElement('main',null,
    React.createElement(ImageCenter,{active:true,draftScope:scope,openTaskId:null,onPrepareVideos:()=>{throw Error('Unexpected video submission')},onNotifications:()=>{},onActiveTasksChange:()=>{}})))));
</script></body></html>`
const buildDir = process.env.UI_IMAGE_BATCH_DESIGN_BUILD_DIR
const server = buildDir ? await preview({ configFile: false, envFile: false, root: resolve(root, 'frontend'), build: { outDir: resolve(buildDir) }, preview: { host: '127.0.0.1', port: 0 } }) : await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error', server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'image-layout-fixture', configureServer(vite) {
    vite.middlewares.use('/__image_layout', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__image_layout', html)) }
      catch (error) { next(error) }
    })
  } }],
})
const report = { checks: [], errors: [], writes: [], unexpected: [] }
let browser
try {
  if (!buildDir) await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const width of [1440, 1024, 821, 810, 768, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: 960 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    if (buildDir) await page.addInitScript(() => {
      window.readDraft = () => new Promise((resolve, reject) => {
        const request = indexedDB.open('clothing-production-image-center', 1)
        request.onerror = () => reject(request.error)
        request.onsuccess = () => {
          const database = request.result
          const transaction = database.transaction('drafts', 'readonly')
          const read = transaction.objectStore('drafts').get('image-center:v2:00000000-0000-0000-0000-000000000099')
          read.onsuccess = () => resolve(read.result)
          read.onerror = () => reject(read.error)
          transaction.oncomplete = () => database.close()
        }
      })
    })
    const fixtures = createPageResponses({ empty: true })
    page.on('pageerror', error => report.errors.push(error.message))
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== origin) { report.unexpected.push(url.origin); return route.abort() }
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-18', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0, unpricedCount: 0, prices: { imagePriceUsd: 0.1, videoPricePerSecondUsd: 0.1, depthVideoPricePerSecondUsd: 0.1, recreateVideoPricePerSecondUsd: 0.1 } } })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { sites: [], defaultProfileId: null } })
      if (url.pathname === '/api/v1/library/kling/elements') return route.fulfill({ json: { items: [], pageNum: 1, pageSize: 100, total: 0 } })
      if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], nextOffset: 0, hasMore: false } })
      if (url.pathname.includes('/prompt-presets')) return route.fulfill({ json: [] })
      if (url.pathname.startsWith('/api/')) { report.unexpected.push(url.pathname); return route.abort() }
      return route.continue()
    })
    await page.goto(buildDir ? `${origin}/?view=images` : `${origin}/__image_layout`)
    await page.getByRole('heading', { name: '产品套装 · 批量生成', exact: true }).waitFor()
    if (process.env.UI_IMAGE_PROMPTS_CHECK) {
      await page.locator('.image-brief-section > .generation-advanced > summary').click()
      const fusion = page.getByRole('textbox', { name: '换装图片 · 完整提示词', exact: true })
      const storyboard = page.getByRole('textbox', { name: '视频分镜 · 完整提示词', exact: true })
      assert((await fusion.inputValue()).startsWith('以图一为底图，仅将图一人物身上的上衣替换为图二的球衣'))
      assert((await storyboard.inputValue()).endsWith('人物高度和透视与环境的关系参考图一的身高'))
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Prompt fields overflow')
      await page.screenshot({ path: resolve(reportDir, `prompts-${width}.png`), fullPage: true })
      report.checks.push({ width, originalPrompts: true })
      await context.close()
      continue
    }
    const geometry = await page.evaluate(() => {
      const style = selector => { const s=getComputedStyle(document.querySelector(selector)); return {background:s.backgroundColor,border:s.borderTopColor,radius:s.borderRadius,shadow:s.boxShadow,position:s.position} }
      return {section:style('.asset-composer'),panel:style('.model-pack-column'),product:style('.outfit-card'),command:style('.image-center-command-bar')}
    })
    assert.equal(geometry.section.background,'rgb(248, 250, 252)')
    assert.equal(geometry.section.border,'rgb(224, 231, 240)')
    assert.equal(geometry.section.radius,'10px')
    assert.equal(geometry.panel.border,'rgb(221, 229, 239)')
    assert.equal(geometry.panel.background,'rgb(255, 255, 255)')
    assert.equal(geometry.product.radius,'8px')
    assert.equal(geometry.command.position,'fixed')
    assert.notEqual(geometry.command.shadow,'none')
    async function checkFloatingBar(state) {
      const measurements = []
      for (const atBottom of [false, true]) {
        await page.evaluate(atBottom => window.scrollTo({ top: atBottom ? document.documentElement.scrollHeight : 0, behavior: 'instant' }), atBottom)
        const bounds = await page.locator('.image-center-command-bar').evaluate(element => {
          const area = element.getBoundingClientRect()
          const button = element.querySelector('.primary-button').getBoundingClientRect()
          return { top: area.top, bottom: area.bottom, left: area.left, right: area.right, buttonTop: button.top, buttonBottom: button.bottom, buttonLeft: button.left, buttonRight: button.right, viewportWidth: innerWidth, viewportHeight: innerHeight }
        })
        assert(bounds.top >= 0 && bounds.left >= 0 && bounds.right <= width, `${state}/${width}: floating bar stays inside viewport`)
        assert(bounds.buttonTop >= bounds.top && bounds.buttonBottom <= bounds.bottom && bounds.buttonLeft >= bounds.left && bounds.buttonRight <= bounds.right, `${state}/${width}: generate button fits floating bar`)
        assert.equal(Math.round(bounds.viewportHeight - bounds.bottom), width <= 820 ? 76 : 18, `${state}/${width}: bar clears bottom navigation`)
        measurements.push(bounds)
      }
      assert.equal(measurements[0].bottom, measurements[1].bottom, `${state}/${width}: bar stays fixed while scrolling`)
      await page.screenshot({ path: resolve(reportDir, `${state}-floating-${width}.png`), animations: 'disabled' })
    }
    await checkFloatingBar('empty')
    assert.equal(await page.locator('.image-brief-section > .generation-brief').count(),1)
    assert.equal(await page.locator('.image-brief-section > .generation-advanced').count(),1)
    assert.equal(await page.locator('.image-center-create > .image-center-command-bar').count(),1)
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Empty page horizontal overflow')
    await page.screenshot({ path: resolve(reportDir, `empty-${width}.png`), fullPage: true, animations: 'disabled' })
    if (process.env.UI_IMAGE_BATCH_DESIGN_BASELINE) { await context.close(); continue }
    const picker = page.getByRole('button', { name: /产品套装，/ })
    assert.equal(await picker.getAttribute('aria-expanded'), 'false')
    assert.equal(await page.getByRole('group', { name: '产品套装列表' }).count(), 0)
    const model = page.getByRole('region', { name: '模特图片', exact: true })
    const product = page.getByRole('region', { name: '服装图片', exact: true })
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
    const image = name => ({ name, mimeType: 'image/png', buffer: png })
    const saved = () => page.getByText('草稿已保存', { exact: true }).waitFor()
    const read = () => page.evaluate(() => window.readDraft().then(draft => ({
      model: [draft.model.front?.name, draft.model.back?.name, draft.model.side?.name],
      outfits: draft.outfits.map(item => ({ name: item.name, sku: item.sku, front: item.front?.name, back: item.back?.name })),
      config: draft.config,
    })))
    await model.locator('input[type=file]').setInputFiles([image('model-front.png'), image('model-back.png')])
    await product.locator('input[type=file]').setInputFiles([image('shirt-front.png'), image('shirt-back.png')])
    await picker.click()
    await page.getByRole('textbox', { name: '套装名称', exact: true }).fill('蓝色球衣')
    await page.getByRole('textbox', { name: '商品 SKU', exact: true }).fill('SKU-01')
    await page.keyboard.press('Escape')
    await saved()
    const original = await read()
    await page.getByRole('button', { name: '添加产品', exact: true }).click()
    assert.equal(await picker.getAttribute('aria-expanded'), 'false')
    await product.locator('input[type=file]').setInputFiles(image('second-shirt.png'))
    await saved()
    await page.getByRole('button', { name: '切换到蓝色球衣', exact: true }).click()
    await product.getByRole('img', { name: '参考图 1：shirt-front.png', exact: true }).waitFor()
    assert.equal((await read()).outfits[0].sku, 'SKU-01')
    await page.screenshot({ path: resolve(reportDir, `uploaded-${width}.png`), fullPage: true, animations: 'disabled' })
    // Reordering and removal still edit the same persisted reference roles.
    await model.locator('.ordered-asset-card').first().dragTo(model.locator('.ordered-asset-card').nth(1))
    await saved()
    assert.equal((await read()).model[0], 'model-back.png')
    await model.getByRole('button', { name: '移除参考图 2', exact: true }).click()
    await saved()
    assert.equal((await read()).model[1], undefined)
    await picker.focus(); await page.keyboard.press('Enter')
    await page.screenshot({ path: resolve(reportDir, `picker-${width}.png`), fullPage: true, animations: 'disabled' })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Horizontal overflow')
    await page.keyboard.press('Escape')
    assert.equal(await picker.getAttribute('aria-expanded'), 'false')
    assert(await picker.evaluate(element => element === document.activeElement), 'Escape restores focus')
    await picker.click()
    await page.getByRole('heading', { name: '产品套装 · 批量生成', exact: true }).click()
    assert.equal(await picker.getAttribute('aria-expanded'), 'false')
    await picker.click()
    await page.getByRole('button', { name: /删除套装 02/ }).click()
    await saved()
    assert.equal((await read()).outfits.length, 1)
    await page.reload()
    await model.getByRole('img', { name: '参考图 1：model-back.png', exact: true }).waitFor()
    await saved()
    const restored = await read()
    assert.deepEqual(restored.outfits, original.outfits)
    assert.deepEqual(restored.config, original.config)
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Horizontal overflow')
    await checkFloatingBar('restored')
    await page.locator('.image-brief-section > .generation-advanced > summary').click()
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Advanced settings horizontal overflow')
    await checkFloatingBar('advanced')
    const promptVisible = await page.locator('.image-brief-section').evaluate(element => element.getBoundingClientRect().bottom <= document.querySelector('.image-center-command-bar').getBoundingClientRect().top)
    assert(promptVisible, `${width}: advanced fields can scroll above floating bar`)
    await page.screenshot({path:resolve(reportDir, `advanced-${width}.png`),fullPage:true})
    await page.locator('.image-brief-section > .generation-advanced > summary').click()
    for(let i=0; i<19; i++) await page.getByRole('button',{name:'添加产品',exact:true}).click()
    assert.equal(await page.locator('.outfit-product-tab').count(),20)
    assert(await page.getByRole('button',{name:'添加产品',exact:true}).isDisabled())
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Many products overflow')
    report.checks.push({ width, geometry, upload: true, inlineProducts: true, switchAddDelete: true, keyboardDismiss: true, reorder: true, persistence: true, promptsPreserved: true, productLimit: true, floatingBar: true, noOverlap: true })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.writes, [])
  assert.deepEqual(report.unexpected, [])
} finally {
  await browser?.close()
  if (buildDir) await new Promise((resolve, reject) => server.httpServer.close(error => error ? reject(error) : resolve()))
  else await server.close()
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
}
console.log(JSON.stringify(report))
