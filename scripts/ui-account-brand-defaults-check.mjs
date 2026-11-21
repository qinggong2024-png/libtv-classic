import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE, 'Set PLAYWRIGHT_MODULE')
assert(process.env.UI_BRAND_REPORT_DIR, 'Set UI_BRAND_REPORT_DIR outside both worktrees')
const reportDir = resolve(process.env.UI_BRAND_REPORT_DIR)
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
const profile = fixtures.get('/api/v1/site-style-profiles')[0]
const profiles = ['pagewings', 'guidingcross'].map((id) => ({ ...profile, id, name: id, libraryScope: 'brand', evidenceLevel: 'verified_site' }))
const sites = profiles.map(({ id, name, version }) => ({ id, name, version, siteUrl: `https://www.${id}.com/` }))
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import {ImageCenter} from '/src/features/image-center/ImageCenter.tsx';
import {ProductionComposer} from '/src/features/production/ProductionComposer.tsx';
import {VideoRecreationPanel} from '/src/features/production/VideoRecreationPanel.tsx';
import {CutflowControl} from '/src/features/production/CutflowControl.tsx';
import '/src/styles.css';
const kind=new URLSearchParams(location.search).get('kind');
const image=new File([Uint8Array.from(atob('${png}'),c=>c.charCodeAt(0))],'source.png',{type:'image/png'});
const props={actorId:'fixture-user',active:true,onCreated:()=>{}};
const seed={id:'fixture-seed',storyboards:[image],products:[]};
const view=kind==='images'?React.createElement(ImageCenter,{...props,draftScope:'fixture-user',openTaskId:null,onPrepareVideos:()=>{},onNotifications:()=>{},onActiveTasksChange:()=>{}})
 :kind==='video'?React.createElement(ProductionComposer,{...props,seed,system:${JSON.stringify(system)}})
 :kind==='deep'?React.createElement(VideoRecreationPanel,props)
 :React.createElement(CutflowControl,{taskId:'fixture-task',variantId:'fixture-variant',artifactId:'fixture-artifact'});
createRoot(document.getElementById('root')).render(React.createElement('div',{className:'app-shell'},
 React.createElement('aside',{className:'sidebar'},'衣影工坊'),React.createElement('div',{className:'app-body'},React.createElement('main',null,view))));
</script></body></html>`
const server = await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error', server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'brand-fixture', configureServer(vite) {
    vite.middlewares.use('/__brand_fixture', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__brand_fixture', html)) }
      catch (error) { next(error) }
    })
  } }],
})
const report = { checks: [], errors: [], unexpected: [], submissions: [] }
let browser
try {
  await mkdir(reportDir, { recursive: true })
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  for (const width of [1440, 390]) for (const assigned of [true, false]) for (const kind of ['images', 'video', 'deep', 'cutflow']) {
    console.log(`Checking ${kind}, assigned=${assigned}, width=${width}`)
    const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    page.on('pageerror', (error) => report.errors.push(error.message))
    await page.addInitScript(() => {
      localStorage.setItem('fixed-site-profile', 'guidingcross')
      localStorage.setItem('fixed-custom-prompt', '已有自定义提示词原文')
    })
    await page.route('**/*', async (route) => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== origin) { report.unexpected.push(url.origin); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (request.method() === 'POST') {
        if (url.pathname === '/api/v1/uploads') return route.fulfill({ json: { id: 'upload-fixture', originalFilename: 'source.png', sha256: 'fixture', width: 1, height: 1, mimeType: 'image/png', sizeBytes: 68, thumbnailUrl: '/fixture.png' } })
        if (url.pathname === '/api/v1/video-tasks') {
          const payload = request.postDataJSON()
          report.submissions.push({ kind, assigned, payload })
          return route.fulfill({ status: 201, json: { ...fixtures.get('/api/v1/tasks')[1], siteProfileId: payload.siteProfileId } })
        }
        if (url.pathname === '/api/v1/image-generation-batches') {
          const data = await new Request(request.url(), { method: 'POST', headers: request.headers(), body: request.postDataBuffer() }).formData()
          const manifest = JSON.parse(data.get('clientManifest'))
          report.submissions.push({ kind, assigned, payload: manifest })
          return route.fulfill({ status: 201, json: { id: 'fixture-batch', clientBatchId: manifest.clientBatchId, configuration: { ...manifest }, provider: manifest.provider, status: 'completed', items: manifest.items.map((item) => ({ ...item, id: item.clientTaskId, status: 'completed', providerTaskId: null, outputs: [], modelId: 'fixture' })) } })
        }
        if (url.pathname === '/api/v1/cutflow-handoffs/source-site') {
          report.submissions.push({ kind, assigned, payload: request.postDataJSON() })
          return route.fulfill({ json: { profileId: request.postDataJSON().siteProfileId } })
        }
        report.unexpected.push(`${request.method()} ${url.pathname}`)
        return route.abort()
      }
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') return route.fulfill({ json: { defaultProfileId: assigned ? 'pagewings' : null, sites: assigned ? sites.slice(0, 1) : sites } })
      if (url.pathname === '/api/v1/cutflow-handoffs') return route.fulfill({ json: { enabled: true, siteRequired: true, handoffs: [] } })
      if (url.pathname === '/api/v1/site-style-profiles') return route.fulfill({ json: profiles })
      if (url.pathname === '/api/v1/image-generation-batches') return route.fulfill({ json: { items: [], hasMore: false, nextOffset: 0 } })
      if (url.pathname === '/api/v1/video-tasks/deep/status') return route.fulfill({ json: { configured: true, enabled: true, reason: null } })
      if (url.pathname === '/api/v1/library/kling/elements') return route.fulfill({ json: { items: [], pageNum: 1, pageSize: 100, total: 0 } })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      report.unexpected.push(`${request.method()} ${url.pathname}`)
      return route.abort()
    })
    const choicesLoaded = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/cutflow-handoffs/sites')
    await page.goto(`${origin}/__brand_fixture?kind=${kind}`)
    await choicesLoaded
    const brand = page.getByRole('combobox', { name: '商品所属品牌', exact: true })
    if (assigned) {
      await page.locator('.cutflow-site-field').waitFor({ state: 'hidden' })
      assert.equal(await brand.count(), 0, `${kind}: assigned brand needs no selector`)
    } else {
      await brand.locator('option[value="pagewings"]').waitFor({ state: 'attached' })
      assert.equal(await brand.inputValue(), '', `${kind}: unassigned account must choose a brand`)
      const action = kind === 'images' ? page.getByRole('button', { name: /生成暂不可用/ }) : kind === 'video' ? page.getByRole('button', { name: /开始生成/ }) : kind === 'cutflow' ? page.getByRole('button', { name: '保存站点关联' }) : page.locator('.video-recreation-footer button')
      assert(await action.isDisabled(), `${kind}: selection is required`)
      await brand.selectOption('guidingcross')
    }
    const selected = assigned ? 'pagewings' : 'guidingcross'
    if (!assigned) assert.equal(await brand.inputValue(), selected)
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${kind}/${width}: no overflow`)
    await page.screenshot({ path: resolve(reportDir, `${kind}-${assigned ? 'assigned' : 'unassigned'}-${width}.png`), fullPage: true })
    if (kind === 'video') {
      await page.locator('.generation-advanced > summary').click()
      const style = page.getByRole('combobox', { name: /^站点风格/ })
      assert.equal(await style.inputValue(), selected)
      if (assigned) assert.equal(await style.locator('option').count(), 2)
      await page.getByRole('radio', { name: /自定义提示词/ }).click()
      if (!assigned) assert.equal(await brand.inputValue(), selected)
      assert.equal(await page.getByRole('textbox', { name: /提示词/ }).inputValue(), '已有自定义提示词原文')
      const submitted = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/video-tasks' && response.request().method() === 'POST')
      await page.getByRole('button', { name: /开始生成/ }).click()
      await submitted
      assert.equal(report.submissions.at(-1).payload.siteProfileId, selected)
      assert.equal(report.submissions.at(-1).payload.customPrompt, '已有自定义提示词原文')
    } else if (kind === 'images') {
      const file = { name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') }
      await page.locator('#image-center-model-files').setInputFiles(file)
      await page.locator('.outfit-upload-panel input[type="file"], .outfit-card input[type="file"], .ordered-asset-upload input[type="file"]').last().setInputFiles(file)
      const submitted = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/image-generation-batches' && response.request().method() === 'POST')
      await page.getByRole('button', { name: /开始生成图片/ }).click()
      await submitted
      assert.equal(report.submissions.at(-1).payload.siteProfileId, selected)
    } else if (kind === 'cutflow') {
      const submitted = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith('/source-site'))
      await page.getByRole('button', { name: '保存站点关联' }).click()
      await submitted
      assert.equal(report.submissions.at(-1).payload.siteProfileId, selected)
      await page.getByRole('button', { name: '送入剪辑', exact: true }).waitFor()
      assert(await page.getByRole('button', { name: '送入剪辑', exact: true }).isEnabled())
    } else {
      await page.getByRole('radio', { name: /深度提取与复刻/ }).check()
      await page.getByRole('group', { name: '深度操作步骤' }).getByRole('button', { name: /提取深度视频/ }).click()
      if (!assigned) assert.equal(await brand.inputValue(), selected, 'Depth mode keeps brand')
      else assert.equal(await brand.count(), 0, 'Depth mode keeps assigned brand hidden')
    }
    report.checks.push({ width, assigned, kind, selected })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.unexpected, [])
  console.log(JSON.stringify({ checks: report.checks.length, simulatedSubmissions: report.submissions.length, errors: report.errors }, null, 2))
} finally {
  if (browser) await browser.close()
  await server.close()
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
}
