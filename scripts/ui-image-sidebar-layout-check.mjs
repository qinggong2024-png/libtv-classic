import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_IMAGE_SIDEBAR_REPORT_DIR, 'Set an external UI_IMAGE_SIDEBAR_REPORT_DIR')
const reportDir = resolve(process.env.UI_IMAGE_SIDEBAR_REPORT_DIR)
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
import {GenerationBudgetContext} from '/src/generationBudget.ts';
const budget={day:'2026-10-22',currency:'USD',prices:{imagePriceUsd:0.1}};
window.readDraft=()=>loadImageCenterDraft('image-sidebar-layout');
createRoot(document.getElementById('root')).render(React.createElement('div',{className:'app-shell'},
  React.createElement('aside',{className:'sidebar'},React.createElement('strong',null,'衣影工坊')),
  React.createElement('div',{className:'app-body'},React.createElement('main',null,
    React.createElement(GenerationBudgetContext.Provider,{value:{budget,failed:false}},React.createElement(ImageCenter,{active:true,draftScope:'image-sidebar-layout',openTaskId:null,onPrepareVideos:()=>{throw Error('Unexpected video submission')},onNotifications:()=>{},onActiveTasksChange:()=>{}}))))));
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
  for (const width of [1440, 1280, 1024, 810, 390, 320]) {
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
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { sites: [{id:'fixture-brand',name:'验收品牌'}], defaultProfileId: null } })
      if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], nextOffset: 0, hasMore: false } })
      if (url.pathname.includes('/prompt-presets')) return route.fulfill({ json: [] })
      if (url.pathname.startsWith('/api/')) { report.unexpected.push(url.pathname); return route.abort() }
      return route.continue()
    })
    await page.goto(`${origin}/__image_upload`)
    await page.getByText('草稿已保存', { exact: true }).waitFor()
    await page.waitForFunction(() => window.readDraft().then(Boolean))
    const settings = page.getByRole('region', { name: '生成设置', exact: true })
    const button = settings.locator('.generation-priced-button')
    assert(await button.isDisabled(), 'Brand gate remains active')
    await page.getByRole('combobox', { name: '商品所属品牌', exact: true }).selectOption('fixture-brand')
    await button.getByText('开始生成', { exact: true }).waitFor()
    assert(await button.isEnabled(), 'Available service and selected brand enable configuration submission')
    let initial
    for (let attempt = 0; attempt < 30 && !initial; attempt++) {
      initial = await page.evaluate(() => window.readDraft())
      if (!initial) await new Promise(resolve => setTimeout(resolve, 100))
    }
    assert(initial, 'Initial draft is persisted before comparing configuration')
    await page.getByRole('button', { name: '16:9', exact: true }).click()
    await page.getByRole('combobox', { name: '分辨率', exact: true }).selectOption('1k')
    await page.getByRole('combobox', { name: '统一结果数量', exact: true }).selectOption('4')
    await page.locator('.generation-brief textarea').fill('界面验收：自然光')
    await page.getByText('草稿已保存', { exact: true }).waitFor()
    const draft = await page.evaluate(() => window.readDraft())
    assert.equal(draft.config.aspectRatio, '16:9')
    assert.equal(draft.config.resolution, '1k')
    assert.equal(draft.config.resultCount, 4)
    assert.deepEqual({...draft.config, aspectRatio:initial.config.aspectRatio, resolution:initial.config.resolution, resultCount:initial.config.resultCount, creativeBrief:initial.config.creativeBrief}, initial.config, 'Layout preserves effective prompts')
    for (const name of ['模特图片', '服装图片']) {
      await page.getByRole('region', { name, exact: true }).locator('input[type=file]').setInputFiles(image(`${name}.png`))
    }
    await page.getByText('草稿已保存', { exact: true }).waitFor()
    await button.locator('.generation-cost-badge').getByText('预估 $0.40', { exact: true }).waitFor()
    const geometry = await page.evaluate(() => {
      const q = selector => document.querySelector(selector)
      const rect = selector => { const b=q(selector).getBoundingClientRect(); return {x:b.x,y:b.y,right:b.right,bottom:b.bottom,width:b.width,height:b.height} }
      const badge=getComputedStyle(q('.generation-cost-badge'))
      return { materials:rect('.image-center-materials'), settings:rect('.image-brief-section'), footer:rect('.image-center-command-bar'), button:rect('.generation-priced-button'), position:getComputedStyle(q('.image-center-command-bar')).position, badge:{background:badge.backgroundColor,color:badge.color}, buttonColor:getComputedStyle(q('.generation-priced-button')).color, overflow:document.documentElement.scrollWidth>innerWidth+1 }
    })
    assert.equal(geometry.position, 'static', 'Action stays within settings flow')
    assert.equal(Math.round(geometry.button.height), 48, 'Compact 48px generate button')
    assert(geometry.button.bottom <= geometry.settings.bottom && geometry.footer.y >= geometry.settings.y)
    assert.equal(geometry.badge.background, 'rgba(255, 255, 255, 0.18)', 'Original translucent cost badge')
    assert.equal(geometry.badge.color, geometry.buttonColor)
    assert(!geometry.overflow, 'No horizontal overflow')
    if(width>1180) assert(geometry.settings.x>=geometry.materials.right && Math.abs(geometry.settings.y-geometry.materials.y)<1, 'Desktop has two top-aligned columns')
    else assert(geometry.settings.y>=geometry.materials.bottom, 'Narrow view stacks settings after materials')
    assert.equal(await page.getByText('本批次检查', {exact:true}).count(),0)
    assert.equal(await button.locator('.lucide-wand-sparkles').count(),1)
    await page.screenshot({path:resolve(reportDir,`sidebar-${width}.png`),fullPage:true,animations:'disabled'})
    // Missing references must remain actionable, without any generation request.
    await page.getByRole('region', { name: '模特图片', exact: true }).getByRole('button', {name:'移除参考图 1',exact:true}).click()
    await button.click()
    await page.locator('.image-center-command-bar > a').click()
    await page.waitForFunction(() => {
      const r = document.querySelector('.model-pack-column').getBoundingClientRect()
      return r.bottom > 0 && r.top < innerHeight
    })
    const modelVisible=await page.getByRole('region',{name:'模特图片',exact:true}).evaluate(el=>{const r=el.getBoundingClientRect();return r.bottom>0&&r.top<innerHeight})
    assert(modelVisible, 'Validation link returns to missing references')
    report.checks.push({width,...geometry, costUpdates:true, settingsPersist:true, promptDefaultsPreserved:true, validationLink:true})
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.writes, [])
  assert.deepEqual(report.unexpected, [])
} finally {
  await browser?.close()
  await server.close()
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report,null,2))
}
console.log(JSON.stringify({passed:report.checks.length,reportDir}))
