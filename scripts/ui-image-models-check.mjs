import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE to the installed playwright entry')
assert(process.env.UI_IMAGE_MODELS_REPORT_DIR, 'Set UI_IMAGE_MODELS_REPORT_DIR outside the worktree')
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const reportDir = resolve(process.env.UI_IMAGE_MODELS_REPORT_DIR)
await mkdir(reportDir, { recursive: true })

// Isolated IndexedDB and synthetic images; every API request is intercepted.
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ImageCenter} from '/src/features/image-center/ImageCenter.tsx';
import {createImageCenterDraft,buildWaitingTasks} from '/src/imageCenterModel.ts';
import {saveImageCenterDraft,loadImageCenterDraft} from '/src/imageCenterStore.ts';
import '/src/styles.css';
const scope='image-model-upgrade-fixture';
const canvas=document.createElement('canvas'); canvas.width=24; canvas.height=32;
canvas.getContext('2d').fillRect(0,0,24,32);
const blob=await new Promise(resolve=>canvas.toBlob(resolve));
const image=name=>new File([blob],name,{type:'image/png'});
const draft=createImageCenterDraft('fixture-draft','fixture-outfit');
draft.model={front:image('model-front.png'),back:image('model-back.png'),side:image('model-side.png')};
Object.assign(draft.outfits[0],{front:image('product-front.png'),back:image('product-back.png'),side:image('product-side.png')});
draft.config.fusionPrompt='只替换服装，保留人物和场景';
draft.config.allowBackInference=false;
draft.tasks=['gpt','nanobanana'].map(provider=>({...buildWaitingTasks(draft,()=>crypto.randomUUID())[0],
  id:'legacy-'+provider,provider,status:'failed',error:'合成历史记录',
  modelId:provider==='gpt'?'openai/gpt-image-2/edit':'google/nano-banana-2/edit'}));
await saveImageCenterDraft(scope,draft);
window.readDraft=()=>loadImageCenterDraft(scope);
createRoot(document.getElementById('root')).render(React.createElement(ImageCenter,{active:true,draftScope:scope,openTaskId:null,onPrepareVideos:()=>{},onNotifications:()=>{},onActiveTasksChange:()=>{}}));
</script></body></html>`

const server = await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error',
  server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'image-model-fixture', configureServer(vite) {
    vite.middlewares.use('/__image_models_fixture', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__image_models_fixture', html)) }
      catch (error) { next(error) }
    })
  } }],
})
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const results = []
  for (const width of [1102, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 884 } })
    const page = await context.newPage()
    const errors = [], external = [], submissions = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== origin) { external.push(url.origin); await route.abort(); return }
      if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
      if (request.method() === 'POST' && url.pathname === '/api/v1/image-generation-batches') {
        const match = request.postDataBuffer().toString().match(/name="clientManifest"\r\n\r\n([\s\S]*?)\r\n--/)
        assert(match, 'Submission must contain a multipart client manifest')
        submissions.push(JSON.parse(match[1]))
        // A deterministic rejection avoids polling and never creates a real task.
        await route.fulfill({ status: 400, json: { detail: { code: 'SYNTHETIC_REJECTION', message: '合成请求已检查', retryable: false } } })
      } else if (request.method() !== 'GET') {
        throw new Error(`Unexpected write: ${request.method()} ${url.pathname}`)
      } else if (url.pathname === '/api/v1/system/status') await route.fulfill({ json: {} })
      else if (url.pathname === '/api/v1/image-generation-batches') await route.fulfill({ json: { items: [], nextOffset: 0, hasMore: false } })
      else if (url.pathname.includes('/prompt-presets')) await route.fulfill({ json: [] })
      else await route.fulfill({ status: 404, json: { detail: 'No live API in fixture' } })
    })
    await page.goto(`${origin}/__image_models_fixture`)
    await page.getByText('草稿已保存', { exact: true }).waitFor()
    const engines = page.locator('.image-provider-control')
    await engines.getByRole('button', { name: 'GPT Image 2.5 Flare WaveSpeed 服务' }).waitFor()
    await engines.getByRole('button', { name: 'Nano Banana 2.1 WaveSpeed 服务' }).waitFor()
    await engines.scrollIntoViewIfNeeded()
    await page.screenshot({ path: resolve(reportDir, `engines-${width}.png`) })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))

    await page.getByRole('button', { name: /^生图记录/ }).click()
    assert.equal(await page.locator('#image-task-legacy-gpt .image-task-meta > span').first().innerText(), 'GPT Image 2')
    assert.equal(await page.locator('#image-task-legacy-nanobanana .image-task-meta > span').first().innerText(), 'Nano Banana 2')
    await page.locator('#image-task-legacy-gpt').getByRole('button', { name: '重新生成' }).click()
    assert((await engines.getByRole('button', { name: 'GPT Image 2.5 Flare WaveSpeed 服务' }).getAttribute('class')).includes('is-selected'))

    for (const [provider, label, modelId] of [
      ['gpt', 'GPT Image 2.5 Flare', 'openai/gpt-image-2.5-flare/edit'],
      ['nanobanana', 'Nano Banana 2.1', 'google/nano-banana-2.1/edit'],
    ]) {
      await page.getByRole('button', { name: '批量创作', exact: true }).click()
      await engines.getByRole('button', { name: `${label} WaveSpeed 服务` }).click()
      const count = submissions.length
      await page.getByRole('button', { name: /创建生图任务/ }).click()
      await page.waitForFunction(() => document.body.innerText.includes('合成请求已检查'))
      assert.equal(submissions.length, count + 1)
      const manifest = submissions.at(-1)
      assert.equal(manifest.provider, provider)
      assert.equal(manifest.items[0].referenceFileIndexes.length, 6)
      assert.equal(manifest.quality, provider === 'gpt' ? 'medium' : undefined)
      await page.getByText('草稿已保存', { exact: true }).waitFor()
      const draft = await page.evaluate(() => window.readDraft())
      assert.equal(draft.tasks[0].modelId, modelId)
      assert.equal(draft.tasks.find(task => task.id === 'legacy-gpt').modelId, 'openai/gpt-image-2/edit')
    }
    assert.deepEqual(errors, [])
    assert.deepEqual(external, [])
    results.push({ width, providers: submissions.map(item => item.provider), legacyLabelsPreserved: true, regenerationUpgraded: true, external, errors })
    await context.close()
  }
  await writeFile(resolve(reportDir, 'result.json'), JSON.stringify({ root, browserVersion: browser.version(), results, liveProviderCalls: 0 }, null, 2))
  console.log('Image engine selection, multipart submissions, six references, legacy history and regeneration passed at 1102px and 390px')
} finally {
  await browser?.close()
  await server.close()
}
