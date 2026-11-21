import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_HISTORY_REPORT_DIR, 'Set UI_HISTORY_REPORT_DIR outside the checkout')
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const reportDir = resolve(process.env.UI_HISTORY_REPORT_DIR)
await mkdir(reportDir, { recursive: true })

const makeBatch = (id, name) => ({
  id, name, clientBatchId: `client-${id}`, provider: 'nanobanana', status: 'completed', createdAt: '2026-10-15T10:00:00Z',
  configuration: { modes: ['fusion'], resultCount: 1, aspectRatio: '9:16', resolution: '2k', quality: 'medium', allowBackInference: false },
  items: [{ id: `item-${id}`, clientTaskId: `task-${id}`, outfitId: `outfit-${id}`, outfitName: name, sku: '',
    status: 'completed', providerTaskId: 'fixture-remote', providerTaskIds: ['fixture-remote'], modelId: 'fixture-model',
    prompts: { fusion: '保留原商品', storyboard: '' }, outputs: [{ id: `output-${id}`, kind: 'fusion', view: 'front', label: `结果 ${id}`,
      url: `/api/v1/image-generation-outputs/output-${id}`, qualificationStatus: 'QUALIFIED' }],
  }],
})
const firstBatch = makeBatch('first', '跨浏览器历史球衣')
const secondBatch = makeBatch('second', '更早的生图记录')
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ImageCenter} from '/src/features/image-center/ImageCenter.tsx';
import {ImageHistoryPicker} from '/src/features/production/ImageHistoryPicker.tsx';
import {createImageCenterDraft} from '/src/imageCenterModel.ts';
import {mergeImageHistory} from '/src/imageCenterHistory.ts';
import {loadImageCenterDraft,saveImageCenterDraft} from '/src/imageCenterStore.ts';
import '/src/styles.css';
const params=new URLSearchParams(location.search); const scope=params.get('scope')||'history-owner';
window.readFixtureDraft=()=>loadImageCenterDraft(scope);
if(params.has('local')) {
  const draft=createImageCenterDraft('local-draft','local-outfit');draft.activeTab='results';draft.name='本地未提交草稿';
  draft.config.fusionPrompt='本地草稿提示词';draft.tasks=mergeImageHistory([], [${JSON.stringify(firstBatch)}]);
  const file=new File(['reference'],'local-reference.png',{type:'image/png'});draft.model.front=file;draft.outfits[0].front=file;
  draft.tasks[0].model.front=file;draft.tasks[0].product.front=file;await saveImageCenterDraft(scope,draft);
}
const props=params.has('picker') ? {scope,remaining:2,addedOutputIds:[],onAdd:images=>{window.addedHistory=images.map(i=>i.sourceImageOutputId)},onClose:()=>{}} :
 {active:true,draftScope:scope,openTaskId:null,onPrepareVideos:handoffs=>{window.videoProductCounts=handoffs.map(h=>[h.task.product?.front,h.task.product?.back,h.task.product?.side].filter(Boolean).length)},onNotifications:()=>{},onActiveTasksChange:()=>{}};
createRoot(document.getElementById('root')).render(React.createElement(params.has('picker')?ImageHistoryPicker:ImageCenter,props));
</script></body></html>`
const server = await createServer({ configFile: false, envFile: false, cacheDir: resolve(reportDir, 'vite-cache'), root: resolve(root, 'frontend'), server: { host: '127.0.0.1', port: 0 }, logLevel: 'error', plugins: [{ name: 'history-fixture', configureServer(vite) {
  vite.middlewares.use('/__history_fixture', async (_req, res, next) => {
    try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__history_fixture', html)) }
    catch (error) { next(error) }
  })
} }] })
let browser
const errors = []
const writes = []
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  async function openFixture(query = '') {
    const context = await browser.newContext()
    const page = await context.newPage()
    const state = { fail: false, review: 'QUALIFIED', offsets: [] }
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      assert.equal(url.origin, origin, 'External network is blocked in this fixture')
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (request.method() !== 'GET') { writes.push(request.method() + ' ' + url.pathname); return route.abort() }
      if (url.pathname === '/api/v1/system/status') return route.fulfill({ json: {} })
      if (url.pathname === '/api/v1/image-generation-batches') {
        const offset = Number(url.searchParams.get('offset') || 0)
        state.offsets.push(offset)
        if (state.fail) return route.fulfill({ status: 503, json: { detail: 'fixture outage' } })
        if (query.includes('scope=other')) return route.fulfill({ json: { items: [], hasMore: false, nextOffset: 0 } })
        const batch = structuredClone(offset ? secondBatch : firstBatch)
        batch.items[0].outputs[0].qualificationStatus = state.review
        return route.fulfill({ json: { items: [batch], hasMore: offset === 0, nextOffset: offset ? 2 : 1 } })
      }
      if (url.pathname.startsWith('/api/v1/image-generation-outputs/')) return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64') })
      return route.fulfill({ status: 404, json: { detail: 'Fixture blocks live APIs' } })
    })
    await page.goto(origin + '/__history_fixture' + query)
    return { context, page, state }
  }
  for (let index = 0; index < 2; index++) {
    const { context, page, state } = await openFixture()
    await page.getByRole('button', { name: /^生图记录/ }).click()
    await page.locator('.image-task-title strong', { hasText: '跨浏览器历史球衣' }).waitFor()
    assert.equal(await page.locator('.image-task-card').count(), 1)
    if (index === 0) {
      await page.getByRole('button', { name: '勾选结果 first', exact: true }).click()
      await page.getByRole('button', { name: '送入视频队列', exact: true }).click()
      await page.waitForFunction(() => window.videoProductCounts?.[0] === 0)
      await page.getByRole('button', { name: '取消勾选结果 first', exact: true }).click()
    }
    await page.getByRole('button', { name: '加载更多生图记录', exact: true }).click()
    await page.locator('.image-task-title strong', { hasText: '更早的生图记录' }).waitFor()
    assert.equal(await page.locator('.image-task-card').count(), 2)
    assert.deepEqual(state.offsets, [0, 1])
    state.review = 'REJECTED'
    await page.getByRole('button', { name: '刷新历史', exact: true }).click()
    await page.waitForFunction(async () => (await window.readFixtureDraft())?.tasks
      .find(task => task.batchId === 'first')?.outputs[0]?.qualificationStatus === 'REJECTED')
    assert.equal(await page.locator('.image-task-card').count(), 2)
    state.fail = true
    await page.getByRole('button', { name: '刷新历史', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: '服务端生图历史读取失败' }).waitFor()
    assert.equal(await page.locator('.image-task-card').count(), 2)
    state.fail = false
    await page.getByRole('button', { name: '重试同步', exact: true }).click()
    await page.getByRole('button', { name: '刷新历史', exact: true }).waitFor({ state: 'visible' })
    await page.getByRole('alert').filter({ hasText: '服务端生图历史读取失败' }).waitFor({ state: 'hidden' })
    if (index === 0) for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 })
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Overflow at ${width}`)
      await page.screenshot({ path: resolve(reportDir, `history-${width}.png`), fullPage: true })
    }
    await context.close()
  }
  {
    const { context, page } = await openFixture('?local=1')
    await page.locator('.image-task-title strong', { hasText: '跨浏览器历史球衣' }).waitFor()
    await page.getByRole('button', { name: '刷新历史', exact: true }).isEnabled()
    await page.waitForFunction(async () => {
      const draft = await window.readFixtureDraft()
      return draft?.tasks.length === 1 && draft.tasks[0].model.front instanceof File
        && draft.config.fusionPrompt === '本地草稿提示词' && draft.model.front instanceof File
    })
    assert.equal(await page.locator('.image-task-card').count(), 1)
    await context.close()
  }
  {
    const { context, page } = await openFixture('?scope=other')
    await page.getByRole('button', { name: /^生图记录/ }).click()
    await page.getByRole('heading', { name: '还没有生图记录', exact: true }).waitFor()
    assert.equal(await page.locator('.image-task-card').count(), 0)
    await context.close()
  }
  {
    const { context, page } = await openFixture('?picker=1')
    await page.getByRole('button', { name: '跨浏览器历史球衣 · 结果 first', exact: true }).click()
    await page.getByRole('button', { name: '添加参考图', exact: true }).click()
    await page.waitForFunction(() => window.addedHistory?.[0] === 'output-first')
    await context.close()
  }
  assert.deepEqual(errors, [])
  assert.deepEqual(writes, [])
  await writeFile(resolve(reportDir, 'result.json'), JSON.stringify({ freshBrowserContexts: 2, localDraftPreserved: true, serverReviewStateRefreshed: true, paginationAndDeduplication: true, outageFallbackAndRetry: true, otherAccountEmpty: true, videoPickerWorksWithoutLocalDraft: true, videoHandoffHandlesTasksWithoutProductReferences: true, generationWrites: writes, pageErrors: errors }, null, 2))
  console.log('Cross-browser image history, pagination, local draft retention, outage retry and video picker passed')
} finally {
  await browser?.close()
  await server.close()
}
