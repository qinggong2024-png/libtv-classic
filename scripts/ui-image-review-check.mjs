import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const playwrightPath = process.env.PLAYWRIGHT_MODULE
if (!playwrightPath) throw new Error('Set PLAYWRIGHT_MODULE to the installed playwright entry file')
const { chromium } = createRequire(import.meta.url)(playwrightPath)
const reportDir = process.env.UI_REVIEW_REPORT_DIR
if (!reportDir) throw new Error('Set UI_REVIEW_REPORT_DIR to an external evidence directory')
await mkdir(reportDir, { recursive: true })

// This fixture mounts the real page but never authenticates or uses live APIs.
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ImageCenter} from '/src/features/image-center/ImageCenter.tsx';
import {ImageHistoryPicker} from '/src/features/production/ImageHistoryPicker.tsx';
import {ResultCard} from '/src/features/production/ResultCard.tsx';
import {createImageCenterDraft} from '/src/imageCenterModel.ts';
import {saveImageCenterDraft} from '/src/imageCenterStore.ts';
import '/src/styles.css';
const canvas = document.createElement('canvas'); canvas.width=240; canvas.height=320;
const ctx=canvas.getContext('2d'); ctx.fillStyle='#e5edf0'; ctx.fillRect(0,0,240,320); ctx.fillStyle='#16805e'; ctx.fillRect(65,60,110,220);
const blob = await new Promise(resolve=>canvas.toBlob(resolve));
const file = new File([blob], 'synthetic-product.png', {type:'image/png'});
const productBack = new File([blob], 'synthetic-product-back.png', {type:'image/png'});
const productSide = new File([blob], 'synthetic-product-side.png', {type:'image/png'});
const draft=createImageCenterDraft('fixture-draft','fixture-outfit'); draft.activeTab='results';
draft.tasks=[{id:'fixture-task',outfitId:'fixture-outfit',outfitName:'合成测试服装',sku:'TEST',provider:'gpt',modes:['fusion'],resultCount:2,aspectRatio:'3:4',resolution:'1k',quality:'medium',outputFormat:'png',allowBackInference:false,storyboardShots:[],status:'completed',batchId:null,providerTaskId:null,providerTaskIds:[],modelId:'fixture',createdAt:new Date().toISOString(),prompt:{fusion:'',storyboard:''},model:{front:file,back:null,side:null},product:{front:file,back:productBack,side:productSide},outputs:[1,2].map(n=>({id:'output-'+n,kind:'fusion',view:'front',label:'合成结果 '+n,file,url:'',qualificationStatus:n===1?'PENDING':'REJECTED'})),error:'',errorCode:null,retryable:null}];
await saveImageCenterDraft('fixture-account',draft);
const extra=document.createElement('div'); document.body.append(extra); const extraRoot=createRoot(extra);
window.showPicker=()=>extraRoot.render(React.createElement(ImageHistoryPicker,{scope:'fixture-account',remaining:6,addedOutputIds:[],onAdd:images=>{window.historyInputs=images.map(image=>image.sourceImageOutputId);extraRoot.render(null)},onClose:()=>extraRoot.render(null)}));
window.showVideo=()=>extraRoot.render(React.createElement(ResultCard,{task:{id:'video-task',title:'合成成片',assets:[],updatedAt:'2026-10-15T00:00:00Z',settings:{durationSeconds:10}},variant:{id:'variant',kind:'STABLE',downloadArtifactId:'fixture-video',generationStatus:'SUCCEEDED',finalApprovalStatus:'PENDING'},canReview:true,onPlay:()=>{},onRemix:()=>{},onOpen:()=>{},onFeedbackChanged:()=>{}}));

createRoot(document.getElementById('root')).render(React.createElement(ImageCenter,{active:true,draftScope:'fixture-account',openTaskId:null,onPrepareVideos:async(items,commit)=>{window.prepared=items.flatMap(item=>item.outputs.map(output=>({id:output.id,status:output.qualificationStatus})));window.productCounts=items.map(item=>[item.task.product.front,item.task.product.back,item.task.product.side].filter(Boolean).length);commit()},onNotifications:()=>{},onActiveTasksChange:()=>{}}));
</script></body></html>`
const server = await createServer({ configFile: false, envFile: false, cacheDir: resolve(reportDir, 'vite-cache'), root: resolve(root, 'frontend'), server: { host: '127.0.0.1', port: 0, strictPort: false }, logLevel: 'error', plugins: [{name:'review-fixture',configureServer(vite) {
  vite.middlewares.use('/__review_fixture', async (_req, res, next) => {
    try { res.setHeader('Content-Type','text/html'); res.end(await vite.transformIndexHtml('/__review_fixture', html)) }
    catch (error) { next(error) }
  })
}}] })
let browser
try {
  await server.listen()
  const port = server.httpServer.address().port
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage()
  const writes = []
  const errors = []
  const blockedExternalRequests = []
  const origin = `http://127.0.0.1:${port}`
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) {
      blockedExternalRequests.push(url.origin)
      await route.abort()
    } else if (!url.pathname.startsWith('/api/')) {
      await route.continue()
    } else if (url.pathname === '/api/v1/image-generation-batches') {
      await route.fulfill({json:{items:[],hasMore:false,nextOffset:0}})
    } else if (url.pathname === '/api/v1/system/status') {
      await route.fulfill({json:{}})
    } else if (url.pathname.includes('/quality-decision')) {
      writes.push(route.request().postDataJSON())
      const id = route.request().url().split('/').at(-2)
      await route.fulfill({json:{id,qualificationStatus:writes.at(-1).decision,qualityGates:{},reviewedAt:new Date().toISOString(),humanReviewSeconds:1}})
    } else await route.fulfill({status:404,json:{detail:'Fixture blocks all live APIs'}})
  })
  await page.goto(`http://127.0.0.1:${port}/__review_fixture`)
  await page.getByRole('heading',{name:'图片中心',exact:true}).waitFor()
  await page.getByRole('button',{name:'勾选合成结果 1',exact:true}).click()
  await page.getByRole('button',{name:'勾选合成结果 2',exact:true}).click()
  assert.equal(await page.locator('.image-selection-product em').innerText(),'2/4')
  assert.equal(writes.length,0,'Selection must not write an approval')
  assert.equal(await page.locator('.image-output-card__quality').count(),0)
  assert.equal(await page.getByRole('button',{name:/审核/}).count(),0)
  const compareButton = page.getByRole('button',{name:'并排比较',exact:true})
  await compareButton.click()
  await page.getByRole('heading',{name:'并排比较',exact:true}).waitFor()
  assert.equal(await page.locator('.image-compare-panel__meta').count(),2)
  assert.equal(await page.getByText('查看图片对比',{exact:true}).count(),1)
  await page.keyboard.press('Escape')
  assert(await compareButton.evaluate(el=>el===document.activeElement),'Compare focus was not restored')
  const comparisonViewports = []
  for (const width of [1440,1024,768,390]) {
    await page.setViewportSize({width,height:900})
    await page.screenshot({path:resolve(reportDir,`image-review-${width}.png`),fullPage:true})
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Overflow at ${width}`)
    await compareButton.click()
    const grid = page.locator('.image-compare-grid')
    await grid.waitFor()
    await grid.evaluate(async el => {
      await Promise.all(el.closest('dialog').getAnimations({subtree:true}).map(animation => animation.finished))
    })
    const images = grid.locator('img')
    assert.equal(await images.count(),2)
    for (const image of await images.all()) {
      await image.evaluate(img => img.decode())
      assert(await image.evaluate(img => img.naturalWidth > 0 && img.naturalHeight > 0 &&
        getComputedStyle(img).objectFit === 'contain'), `Uninspectable comparison image at ${width}`)
    }
    const geometry = await grid.evaluate(el => {
      const dialog = el.closest('dialog')
      const rect = dialog.getBoundingClientRect()
      return {dialogScrollWidth:dialog.scrollWidth,dialogClientWidth:dialog.clientWidth,
        gridScrollWidth:el.scrollWidth,gridClientWidth:el.clientWidth,left:rect.left,right:rect.right}
    })
    await page.screenshot({path:resolve(reportDir,`image-compare-${width}.png`),fullPage:true})
    assert(geometry.dialogScrollWidth <= geometry.dialogClientWidth + 1 &&
      geometry.gridScrollWidth <= geometry.gridClientWidth + 1 &&
      geometry.left >= -1 && geometry.right <= width + 1,
    `Comparison overflow at ${width}: ${JSON.stringify(geometry)}`)
    assert.equal(writes.length,0,'Comparison must remain read-only')
    await grid.locator('.image-compare-panel__meta').last().scrollIntoViewIfNeeded()
    assert(await grid.locator('.image-compare-panel__meta').last().evaluate(el => {
      const rect = el.getBoundingClientRect()
      return rect.top >= 0 && rect.bottom <= innerHeight + 1
    }), `Second comparison metadata unreachable at ${width}`)
    if (width === 390) await page.screenshot({path:resolve(reportDir,'image-compare-scrolled-390.png')})
    await page.keyboard.press('Escape')
    assert(await compareButton.evaluate(el=>el===document.activeElement),`Compare focus at ${width}`)
    comparisonViewports.push({width,panels:2,imagesLoaded:true,objectFit:'contain',horizontalOverflow:false,reviewWrites:0,escapeFocusRestored:true})
  }
  await page.getByRole('button',{name:'送入视频队列',exact:true}).click()
  await page.waitForFunction(()=>window.prepared?.length===2)
  assert.deepEqual(await page.evaluate(()=>window.productCounts),[3])
  assert.deepEqual(await page.evaluate(()=>window.prepared),[{id:'output-1',status:'PENDING'},{id:'output-2',status:'REJECTED'}])
  assert.equal(writes.length,0,'Preparing video inputs must not write a review')
  assert.equal(await page.getByText('只有已完成人工审核并标记为合格的图片才能进入视频生成。',{exact:true}).count(),0)
  await page.evaluate(()=>window.showPicker())
  const picker=page.getByRole('dialog')
  await picker.getByRole('button',{name:'合成测试服装 · 合成结果 1',exact:true}).click()
  await picker.getByRole('button',{name:'合成测试服装 · 合成结果 2',exact:true}).click()
  await picker.getByRole('button',{name:'添加参考图',exact:true}).click()
  await page.waitForFunction(()=>window.historyInputs?.length===2)
  assert.deepEqual(await page.evaluate(()=>window.historyInputs),['output-1','output-2'])
  await page.evaluate(()=>window.showVideo())
  const card=page.locator('.result-card')
  await card.waitFor()
  assert(await card.getByRole('button',{name:'下载生成原片',exact:true}).isEnabled())
  assert(!(await card.innerText()).includes('审核'))
  await card.scrollIntoViewIfNeeded()
  await page.screenshot({path:resolve(reportDir,'video-result-390.png'),fullPage:true})
  assert.equal(writes.length,0)
  assert.deepEqual(errors,[])
  assert.deepEqual(blockedExternalRequests,[])
  const sourceHead = execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim()
  const scriptSha256 = createHash('sha256').update(await readFile(fileURLToPath(import.meta.url))).digest('hex')
  await writeFile(resolve(reportDir,'result.json'),JSON.stringify({scope:'isolated-no-manual-review',sourceHead,scriptSha256,browserVersion:browser.version(),capturedAt:new Date().toISOString(),selectionWrites:0,reviewWrites:writes.length,videoInputsPrepared:2,comparePanels:2,compareReadonly:true,compareEscapeFocusRestored:true,viewports:[1440,1024,768,390],comparisonViewports,blockedExternalRequests,pageErrors:errors},null,2))
  console.log('Direct video preparation without review, read-only comparison, and four viewport checks passed')
} finally {
  await browser?.close()
  await server.close()
}
