import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE to the installed playwright entry')
assert(process.env.UI_REUSE_REPORT_DIR, 'Set UI_REUSE_REPORT_DIR to an external evidence directory')
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const reportDir = resolve(process.env.UI_REUSE_REPORT_DIR)
await mkdir(reportDir, { recursive: true })

// Mount the actual component with isolated IndexedDB data. All APIs are mocked.
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ImageCenter} from '/src/features/image-center/ImageCenter.tsx';
import {createImageCenterDraft,createOutfit,buildWaitingTasks,buildImageGenerationBatchRequest,calculateImageTaskSummary,validateImageCenterDraft} from '/src/imageCenterModel.ts';
import {saveImageCenterDraft,loadImageCenterDraft} from '/src/imageCenterStore.ts';
import '/src/styles.css';
const scope='image-reuse-fixture';
const canvas=document.createElement('canvas'); canvas.width=240; canvas.height=320;
const ctx=canvas.getContext('2d'); ctx.fillStyle='#e5edf0'; ctx.fillRect(0,0,240,320); ctx.fillStyle='#16805e'; ctx.fillRect(65,60,110,220);
const blob=await new Promise(resolve=>canvas.toBlob(resolve));
const image=name=>new File([blob],name,{type:'image/png'});
if (!await loadImageCenterDraft(scope)) {
  const draft=createImageCenterDraft('reuse-draft','outfit-1'); draft.activeTab='results';
  draft.model={front:image('original-model.png'),back:image('original-model-back.png'),side:null};
  draft.outfits[0].front=image('product-1.png'); draft.outfits[0].back=image('product-1-back.png');
  const second=createOutfit('outfit-2',2); second.front=image('product-2.png'); draft.outfits.push(second);
  draft.config.provider='gpt'; draft.config.fusionPrompt='保留产品与人物'; draft.config.storyboardPrompt='生成一张侧面展示图片';
  draft.tasks=[{...buildWaitingTasks(draft,()=>crypto.randomUUID())[0],id:'task-1',status:'completed',outputs:[
    {id:'local',kind:'fusion',view:'front',label:'本地结果',file:image('local-result.png'),url:'',qualificationStatus:'PENDING'},
    {id:'remote',kind:'storyboard',view:'shot',label:'远程结果',file:null,url:'/api/v1/downloads/preview',downloadUrl:'/api/v1/downloads/original',qualificationStatus:'REJECTED'},
    {id:'broken',kind:'fusion',view:'front',label:'失效结果',file:null,url:'/api/v1/downloads/missing',qualificationStatus:'PENDING'}
  ]}];
  await saveImageCenterDraft(scope,draft);
}
window.fixture={read:()=>loadImageCenterDraft(scope), request:async()=>{
  const draft=await loadImageCenterDraft(scope);
  const tasks=buildWaitingTasks(draft,()=>crypto.randomUUID());
  const {manifest,files}=buildImageGenerationBatchRequest({clientBatchId:'fixture',name:draft.name,tasks,expectedGenerationCount:calculateImageTaskSummary(draft).totalImages});
  return {manifest,filenames:files.map(file=>file.name),errors:validateImageCenterDraft(draft)};
}};
createRoot(document.getElementById('root')).render(React.createElement(ImageCenter,{active:true,draftScope:scope,openTaskId:null,onPrepareVideos:()=>{},onNotifications:()=>{},onActiveTasksChange:()=>{}}));
</script></body></html>`
const server = await createServer({ configFile: false, envFile: false, cacheDir: resolve(reportDir, 'vite-cache'), root: resolve(root, 'frontend'), server: { host: '127.0.0.1', port: 0 }, logLevel: 'error', plugins: [{ name: 'reuse-fixture', configureServer(vite) {
  vite.middlewares.use('/__reuse_fixture', async (_req, res, next) => {
    try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__reuse_fixture', html)) }
    catch (error) { next(error) }
  })
}}] })
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  const writes = []
  const external = []
  const assetReads = []
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR1sAAAAASUVORK5CYII=', 'base64')
  let failOriginal = false
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) { external.push(url.origin); await route.abort(); return }
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    if (request.method() !== 'GET') writes.push({ method: request.method(), path: url.pathname })
    if (url.pathname === '/api/v1/system/status') await route.fulfill({ json: {} })
    else if (url.pathname === '/api/v1/image-generation-batches') await route.fulfill({ json: { items: [], nextOffset: 0, hasMore: false } })
    else if (url.pathname.includes('/prompt-presets')) await route.fulfill({ json: [] })
    else if (url.pathname.startsWith('/api/v1/downloads/')) {
      assetReads.push(url.pathname)
      if (url.pathname.endsWith('/missing') || (failOriginal && url.pathname.endsWith('/original'))) await route.fulfill({ status: 404, json: { detail: 'Fixture missing image' } })
      else await route.fulfill({ contentType: 'image/png', body: png })
    } else await route.fulfill({ status: 404, json: { detail: 'Live APIs blocked by fixture' } })
  })
  const resultsTab = () => page.getByRole('button', { name: /^生图记录/ })
  const createTab = () => page.getByRole('button', { name: '批量创作', exact: true })
  const saved = () => page.getByText('草稿已保存', { exact: true }).waitFor()
  const read = () => page.evaluate(() => window.fixture.read().then(draft => ({
    model: { front: draft.model.front?.name, back: draft.model.back?.name ?? null, side: draft.model.side?.name ?? null },
    products: draft.outfits.map(outfit => [outfit.id, outfit.front?.name, outfit.back?.name ?? null]),
    config: draft.config, tab: draft.activeTab, tasks: draft.tasks.length,
    reviews: draft.tasks[0].outputs.map(output => output.qualificationStatus), selected: draft.selectedOutputIds,
  })))
  await page.goto(`${origin}/__reuse_fixture`)
  await page.getByRole('button', { name: '继续编辑本地结果', exact: true }).waitFor()
  const baseline = await read()
  await page.screenshot({ path: resolve(reportDir, 'results-1440.png'), fullPage: true })

  // A real native HTML drag goes from the result card onto the creation tab.
  await page.locator('.image-output-card').first().dragTo(createTab())
  await page.getByText(/已将「本地结果」载入/).waitFor()
  await saved()
  let draft = await read()
  assert.deepEqual(draft.model, { front: 'local-result.png', back: null, side: null })
  assert.deepEqual(draft.products, baseline.products)
  assert.deepEqual(draft.config, baseline.config)
  assert.deepEqual(draft.reviews, baseline.reviews)
  assert.equal(draft.tasks, baseline.tasks)
  assert.deepEqual(draft.selected, [])
  assert.equal(draft.tab, 'create')

  await page.getByRole('radio', { name: /分镜图生成/ }).click()
  const storyboard = page.locator('.image-prompt-grid > section').nth(1).locator('textarea')
  await storyboard.fill('生成一张人物走向镜头的分镜图，保持服装一致')
  await saved()
  const request = await page.evaluate(() => window.fixture.request())
  assert.deepEqual(request.errors, [])
  assert.deepEqual(request.manifest.modes, ['storyboard'])
  assert.equal(request.manifest.items.length, 2)
  for (const item of request.manifest.items) {
    const modelRef = item.referenceRoles.find(ref => ref.role === 'model.front')
    assert.equal(request.filenames[modelRef.fileIndex], 'local-result.png')
    assert.match(item.prompts.storyboard, /人物走向镜头/)
  }
  await page.screenshot({ path: resolve(reportDir, 'creation-1440.png'), fullPage: true })
  await page.reload()
  await page.getByRole('heading', { name: '素材编排', exact: true }).waitFor()
  assert.equal((await read()).model.front, 'local-result.png', 'Reused file must survive reload')
  assert.equal(await storyboard.inputValue(), '生成一张人物走向镜头的分镜图，保持服装一致')

  // Clicking works by keyboard, and an authenticated original is fetched even for a rejected image.
  await resultsTab().click()
  const remoteButton = page.getByRole('button', { name: '继续编辑远程结果', exact: true })
  await remoteButton.focus()
  await page.keyboard.press('Enter')
  await page.getByText(/已将「远程结果」载入/).waitFor()
  await saved()
  draft = await read()
  assert.equal(draft.model.front, '远程结果.png')
  assert.deepEqual(draft.products, baseline.products)
  assert.deepEqual(draft.reviews, baseline.reviews)
  assert(assetReads.includes('/api/v1/downloads/original'))

  await resultsTab().click()
  await page.getByRole('button', { name: '继续编辑失效结果', exact: true }).click()
  await page.getByText(/结果图载入失败/).waitFor()
  assert.equal((await read()).model.front, '远程结果.png', 'Failed download must preserve the draft')
  failOriginal = true
  await remoteButton.click()
  await page.getByText(/结果图载入失败/).waitFor()
  failOriginal = false
  await remoteButton.click()
  await page.getByText(/已将「远程结果」载入/).waitFor()
  await saved()

  await resultsTab().click()
  // Invalid and foreign identifiers must never fetch an arbitrary URL or alter the draft.
  for (const payload of ['null', JSON.stringify({ taskId: 'another-account', outputId: 'remote', url: 'https://invalid.example/image.png' })]) {
    const transfer = await page.evaluateHandle(value => {
      const data = new DataTransfer(); data.setData('application/x-image-center-output', value); return data
    }, payload)
    await createTab().dispatchEvent('drop', { dataTransfer: transfer })
    await transfer.dispose()
  }
  assert.equal((await read()).model.front, '远程结果.png')
  assert.equal(await page.locator('.image-results').isVisible(), true)
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer(); data.setData('application/x-image-center-output', JSON.stringify({ taskId: 'task-1', outputId: 'local' })); return data
  })
  await createTab().dispatchEvent('dragover', { dataTransfer: transfer })
  await page.getByRole('button', { name: '松开继续创作', exact: true }).waitFor()
  await page.screenshot({ path: resolve(reportDir, 'drop-feedback-1440.png'), fullPage: true })
  await page.getByRole('button', { name: '松开继续创作', exact: true }).dispatchEvent('dragleave')
  await transfer.dispose()
  await createTab().waitFor()

  // One desktop and one mobile layout, with the real result image and editing action.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: resolve(reportDir, 'results-390.png'), fullPage: true })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile results overflow')
  await page.getByRole('button', { name: '继续编辑本地结果', exact: true }).click()
  await page.getByText(/已将「本地结果」载入/).waitFor()
  await page.screenshot({ path: resolve(reportDir, 'creation-390.png'), fullPage: true })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile creation overflow')
  assert.deepEqual(errors, [])
  assert.deepEqual(writes, [], 'Reuse must never submit a generation or write a review')
  assert.deepEqual(external, [])
  const report = { nativeDrag: true, keyboardReuse: true, remoteDownload: true, failedDownloadPreservesDraft: true, retry: true, malformedDragRejected: true, accountScopeRespected: true, productsAndConfigPreserved: true, storyboardReferencesReusedFile: true, reloadPersistsFile: true, reviewUnchanged: true, desktopAndMobile: true, pageErrors: errors, apiWrites: writes, externalRequests: external }
  await writeFile(resolve(reportDir, 'report.json'), `${JSON.stringify(report, null, 2)}\n`)
  console.log(JSON.stringify(report))
} finally {
  await browser?.close()
  await server.close()
}
