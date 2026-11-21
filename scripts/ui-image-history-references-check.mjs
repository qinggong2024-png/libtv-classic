import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_IMAGE_HISTORY_REPORT_DIR, 'Set an external UI_IMAGE_HISTORY_REPORT_DIR')
const reportDir = resolve(process.env.UI_IMAGE_HISTORY_REPORT_DIR)
for (const tree of [root, resolve(root, '../libtv-classic-0e3d1c8-main')]) {
  const path = relative(tree, reportDir)
  assert(path.startsWith('..') || isAbsolute(path), 'Keep reports outside both worktrees')
}
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })

// Synthetic local history only. No real account, upload, or generation requests.
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import {ImageCenter} from '/src/features/image-center/ImageCenter.tsx';
import {createImageCenterDraft,buildWaitingTasks} from '/src/imageCenterModel.ts';
import {saveImageCenterDraft,loadImageCenterDraft} from '/src/imageCenterStore.ts';
import '/src/styles.css';
const scope='history-reference-fixture';
if (!await loadImageCenterDraft(scope)) {
  const canvas=document.createElement('canvas'); canvas.width=240; canvas.height=320;
  const ctx=canvas.getContext('2d'); ctx.fillStyle='#dbeafe'; ctx.fillRect(0,0,240,320);
  ctx.fillStyle='#1e40af'; ctx.font='32px sans-serif'; ctx.fillText('Reference',24,160);
  const blob=await new Promise(resolve=>canvas.toBlob(resolve));
  const image=name=>new File([blob],name,{type:'image/png'});
  const draft=createImageCenterDraft('fixture-draft','fixture-outfit');
  draft.model.front=image('model-front.png'); draft.outfits[0].front=image('product-front.png');
  const base=buildWaitingTasks(draft,()=>crypto.randomUUID())[0];
  draft.activeTab='results';
  draft.tasks=[0,1,2,3,6].map(count=>{
    const model={front:null,back:null,side:null}, product={front:null,back:null,side:null};
    // Include gaps in the saved slots to cover older history as well as normal uploads.
    const slots=[[model,'front'],[product,'front'],[model,'side'],[product,'back'],[model,'back'],[product,'side']];
    slots.slice(0,count).forEach(([set,slot],i)=>set[slot]=image('reference-'+i+'.png'));
    return {...base,id:'refs-'+count,outfitName:count+' 张参考图',status:'completed',model,product,
      outputs:[{id:'output-'+count,kind:'fusion',view:'front',label:'正面融合图',file:image('result.png'),url:''}]};
  });
  await saveImageCenterDraft(scope,draft);
}
createRoot(document.getElementById('root')).render(React.createElement(ImageCenter,{active:true,canMakeVideos:false,draftScope:scope,openTaskId:null,onPrepareVideos:()=>{throw Error('Unexpected generation')},onNotifications:()=>{},onActiveTasksChange:()=>{}}));
</script></body></html>`
const server = await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error', server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'history-reference-fixture', configureServer(vite) {
    vite.middlewares.use('/__history_references', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__history_references', html)) }
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
  for (const width of [1440, 768, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 960 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    const fixtures = createPageResponses({ empty: true })
    page.on('pageerror', error => report.errors.push(error.message))
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== origin) { report.unexpected.push(url.origin); return route.abort() }
      if (request.method() !== 'GET') { report.writes.push(url.pathname); return route.abort() }
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], nextOffset: 0, hasMore: false } })
      if (url.pathname.includes('/prompt-presets')) return route.fulfill({ json: [] })
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { sites: [], defaultProfileId: null } })
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { prices: {} } })
      if (url.pathname.startsWith('/api/')) { report.unexpected.push(url.pathname); return route.abort() }
      return route.continue()
    })
    await page.goto(`${origin}/__history_references`)
    const checkReferences = async () => {
      await page.locator('#image-task-refs-6').waitFor()
      for (const count of [0,1,2,3,6]) {
        const card = page.locator('#image-task-refs-' + count)
        assert.equal(await card.locator('.image-task-source').count(), count)
        assert.equal(await card.locator('.image-task-sources').count(), count ? 1 : 0)
        assert.equal(await card.locator('.image-task-source button:disabled').count(), 0)
        assert.equal(await card.locator('.image-output-card').count(), 1)
      }
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Horizontal overflow')
    }
    await checkReferences()
    await page.locator('#image-task-refs-2').getByRole('button', { name: '查看模特参考图大图', exact: true }).click()
    await page.getByRole('dialog').waitFor()
    await page.keyboard.press('Escape')
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
    const compact = await page.locator('#image-task-refs-2 .image-task-source').first().evaluate(el => el.getBoundingClientRect().height)
    assert(compact < 130, 'Two references should occupy a single compact row')
    await page.locator('#image-task-refs-2').screenshot({ path: resolve(reportDir, `two-references-${width}.png`), animations: 'disabled' })
    await page.reload()
    await checkReferences()
    report.checks.push({ width, referenceCounts: [0,1,2,3,6], preview: true, reload: true, compact: true })
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
