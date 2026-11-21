import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_IMAGE_UPLOAD_REPORT_DIR, 'Set an external UI_IMAGE_UPLOAD_REPORT_DIR')
const reportDir = resolve(process.env.UI_IMAGE_UPLOAD_REPORT_DIR)
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
import {ImageCenter} from '/src/features/image-center/ImageCenter.tsx';
import {loadImageCenterDraft} from '/src/imageCenterStore.ts';
import '/src/styles.css';
window.readDraft=()=>loadImageCenterDraft('image-upload-layout');
createRoot(document.getElementById('root')).render(React.createElement('div',{className:'app-shell'},
  React.createElement('aside',{className:'sidebar'},React.createElement('strong',null,'衣影工坊')),
  React.createElement('div',{className:'app-body'},React.createElement('main',null,
    React.createElement(ImageCenter,{active:true,draftScope:'image-upload-layout',openTaskId:null,onPrepareVideos:()=>{throw Error('Unexpected video submission')},onNotifications:()=>{},onActiveTasksChange:()=>{}})))));
</script></body></html>`
const server = await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error', server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'image-upload-fixture', configureServer(vite) {
    vite.middlewares.use('/__image_upload', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__image_upload', html)) }
      catch (error) { next(error) }
    })
  } }],
})
const report = { checks: [], errors: [], writes: [], unexpected: [] }
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const image = name => ({ name, mimeType: 'image/png', buffer: png })
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const width of [1440, 1024, 821, 810, 768, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: 960 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    page.on('pageerror', error => report.errors.push(error.message))
    const fixtures = createPageResponses({ empty: true })
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== origin) { report.unexpected.push(url.origin); return route.abort() }
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-21', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0, unpricedCount: 0, prices: { imagePriceUsd: 0.1 } } })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { sites: [], defaultProfileId: null } })
      if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], nextOffset: 0, hasMore: false } })
      if (url.pathname.includes('/prompt-presets')) return route.fulfill({ json: [] })
      if (url.pathname.startsWith('/api/')) { report.unexpected.push(url.pathname); return route.abort() }
      return route.continue()
    })
    await page.goto(`${origin}/__image_upload`)
    const model = page.getByRole('region', { name: '模特图片', exact: true })
    const product = page.getByRole('region', { name: '服装图片', exact: true })
    const saved = () => page.getByText('草稿已保存', { exact: true }).waitFor()
    const read = () => page.evaluate(() => window.readDraft().then(draft => ({
      model: [draft.model.front?.name, draft.model.back?.name, draft.model.side?.name],
      product: [draft.outfits[0].front?.name, draft.outfits[0].back?.name, draft.outfits[0].side?.name],
      config: draft.config,
    })))
    async function checkLayout(count) {
      for (const region of [model, product]) {
        assert.equal(await region.locator('.ordered-asset-card').count(), count)
        const geometry = await region.evaluate(element => {
          const rect = node => { const b = node.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height, right: b.right } }
          return { panel: rect(element), cards: [...element.querySelectorAll('.ordered-asset-card')].map(rect), add: element.querySelector('.ordered-asset-dropzone') ? rect(element.querySelector('.ordered-asset-dropzone')) : null }
        })
        if (!count) assert(geometry.add.width > geometry.panel.width - 2, 'Empty drop target fills the panel')
        if (count) {
          assert(geometry.cards.every(card => Math.abs(card.y - geometry.cards[0].y) < 1), 'References stay on one row')
          assert(geometry.cards.every(card => card.right <= geometry.panel.right + 1), 'References fit the panel')
          if (count < 3) {
            assert(Math.abs(geometry.add.y - geometry.cards[0].y) < 1, 'Add target shares the reference row')
            assert(geometry.add.x > geometry.cards.at(-1).x, 'Add target follows references')
            assert(Math.abs(geometry.add.height - geometry.cards[0].height) < 1, 'Add target matches thumbnail height')
          } else assert.equal(geometry.add, null, 'Full upload hides add target')
        }
      }
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'No horizontal overflow')
      if ([1440, 390, 320].includes(width)) await page.screenshot({ path: resolve(reportDir, `count-${count}-${width}.png`), fullPage: true, animations: 'disabled' })
    }
    async function drop(target, names) {
      const transfer = await page.evaluateHandle(({ bytes, names }) => {
        const data = new DataTransfer()
        for (const name of names) data.items.add(new File([new Uint8Array(bytes)], name, { type: name.endsWith('.png') ? 'image/png' : 'text/plain' }))
        return data
      }, { bytes: [...png], names })
      await target.dispatchEvent('dragover', { dataTransfer: transfer })
      await target.dispatchEvent('drop', { dataTransfer: transfer })
      await transfer.dispose()
      await saved()
    }
    await saved()
    await checkLayout(0)
    // Exercise the click-to-select path for both uploaders.
    for (const [region, name] of [[model, 'model-one.png'], [product, 'product-one.png']]) {
      const chooser = page.waitForEvent('filechooser')
      await region.locator('.ordered-asset-dropzone').click()
      await (await chooser).setFiles(image(name))
    }
    await saved()
    const initialConfig = (await read()).config
    await checkLayout(1)
    // A drag on the tile and a drag on the surrounding uploader both append.
    await drop(model.locator('.ordered-asset-dropzone'), ['model-two.png'])
    await drop(product, ['product-two.png'])
    await checkLayout(2)
    await drop(model, ['ignored.txt'])
    assert.equal(await model.locator('.ordered-asset-card').count(), 2, 'Unsupported files are ignored')
    await drop(model, ['model-three.png', 'model-over-limit.png'])
    await drop(product.locator('.ordered-asset-dropzone'), ['product-three.png'])
    await checkLayout(3)
    await drop(model, ['model-extra.png'])
    assert.equal(await model.locator('.ordered-asset-card').count(), 3, 'Drag upload respects three-image limit')
    // Sorting, removal, saving, and restoration must retain reference roles and prompts.
    await model.locator('.ordered-asset-card').first().dragTo(model.locator('.ordered-asset-card').nth(1))
    await saved()
    assert.equal((await read()).model[0], 'model-two.png')
    await model.getByRole('button', { name: '移除参考图 3', exact: true }).click()
    await saved()
    assert(await model.locator('.ordered-asset-dropzone').isVisible(), 'Removing an image restores add target')
    const beforeReload = await read()
    assert.deepEqual(beforeReload.config, initialConfig, 'Generation instructions and settings are unchanged')
    await page.reload()
    await model.getByRole('img', { name: '参考图 1：model-two.png', exact: true }).waitFor()
    await saved()
    assert.deepEqual(await read(), beforeReload, 'Uploaded files and configuration survive reload')
    report.checks.push({ width, counts: [0, 1, 2, 3], sameRow: true, clickUpload: true, dropOnTile: true, dropOnRegion: true, fileLimit: true, reorder: true, remove: true, persistence: true, promptsPreserved: true })
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
