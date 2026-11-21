import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { mkdir, writeFile, readFile } from 'node:fs/promises'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'
import { pathToFileURL } from 'node:url'

assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_TASK_PAGES_REPORT_DIR, 'Required isolated browser runtime and report dir')
const root = resolve(import.meta.dirname, '..')
const reportDir = resolve(process.env.UI_TASK_PAGES_REPORT_DIR)
await mkdir(reportDir, { recursive: true })
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const frontendRequire = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(frontendRequire.resolve('vite')).href)
const fixtureHtml = '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">import React from "react";import{createRoot}from"react-dom/client";import App from "/src/app/App.tsx";import "/src/styles.css";createRoot(document.getElementById("root")).render(React.createElement(App));</script></body></html>'
const server = await createServer({configFile:false,envFile:false,root:resolve(root,'frontend'),cacheDir:resolve(reportDir,'vite-cache'),logLevel:'error',server:{host:'127.0.0.1',port:0},plugins:[{name:'task-pages-fixture',configureServer(vite){vite.middlewares.use('/__task_pages_fixture',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await vite.transformIndexHtml('/__task_pages_fixture',fixtureHtml))}catch(e){next(e)}})}}]})
await server.listen()
const origin = 'http://127.0.0.1:' + server.httpServer.address().port
const browser = await chromium.launch({channel:process.env.UI_BROWSER_CHANNEL || 'chrome',headless:true})
const fixtures = createPageResponses()
const sourceTask = fixtures.get('/api/v1/tasks').find(task=>task.phase==='DELIVERABLE')
const videoPath = resolve(reportDir, 'synthetic-cut-preview.mp4')
execFileSync('ffmpeg', ['-v','error','-y','-f','lavfi','-i','testsrc2=size=160x288:rate=10','-t','1','-c:v','libx264','-pix_fmt','yuv420p',videoPath],{windowsHide:true})
const videoBytes = await readFile(videoPath)
assert(sourceTask)
const result = {scope:'isolated fixtures; no production writes',origin,tests:[],errors:[],externalRequests:[],unexpectedWrites:[],missingApis:[]}
try {
  for (const width of [1440,1024]) {
    const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce',serviceWorkers:'block'})
    const page=await context.newPage()
    page.on('pageerror',e=>result.errors.push(e.message))
    page.on('console',m=>{if(m.type()==='error')result.errors.push('console: '+m.text())})
    await context.addCookies([{name:'synthetic_csrf',value:'fixture-csrf',url:origin}])
    const batch={id:'synthetic-cut-batch',status:'EDITING',sources:[{clipId:'clip-1',variantId:sourceTask.variants[0].id,taskId:sourceTask.id,artifactId:'synthetic-artifact',title:'合成原片 01',order:1},{clipId:'clip-2',variantId:sourceTask.variants[0].id,taskId:sourceTask.id,artifactId:'synthetic-artifact',title:'合成原片 02',order:2}],outputs:[],createdAt:sourceTask.createdAt,updatedAt:sourceTask.updatedAt,errorMessage:null}
    const done = Array.from({length:2},(_,i)=>({...batch,id:'synthetic-done-'+i,status:'PENDING_REVIEW',updatedAt:'2026-10-29T0'+(8+i)+':00:00Z',outputs:[{id:'output-'+i,reviewStatus:'PENDING',durationSeconds:5.9,width:1080,height:1920,createdAt:'2026-10-29T0'+(8+i)+':00:00Z',mediaUrl:'/api/v1/fixture-media/cut-preview.mp4'}]}))
    const failed = Array.from({length:6},(_,i)=>({...batch,id:'synthetic-failed-'+i,status:i===5?'CANCELED':'FAILED',updatedAt:'2026-10-28T0'+(2+i)+':00:00Z',errorMessage:'隔离测试：历史剪辑失败 '+i,outputs:[]}))
    await context.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url())
      if(url.origin!==origin){result.externalRequests.push(url.href);return route.abort()}
      if(!url.pathname.startsWith('/api/'))return route.continue()
      if(request.method()!=='GET'){result.unexpectedWrites.push(url.pathname);return route.fulfill({status:403,json:{detail:'No writes in isolated fixture'}})}
      if(url.pathname==='/api/v1/cutflow-handoffs/multi')return route.fulfill({json:{tasks:[batch,...done,...failed]}})
      if(url.pathname==='/api/v1/fixture-media/cut-preview.mp4')return route.fulfill({contentType:'video/mp4',body:videoBytes})
      if(url.pathname==='/api/v1/cutflow-handoffs')return route.fulfill({json:{enabled:true,siteRequired:false,handoffs:[]}})
      if(url.pathname.endsWith('/thumbnail')||url.pathname==='/api/v1/fixture-media/product.png')return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="288"><rect width="160" height="288" fill="#d6e0ea"/><path d="M56 40L35 70L45 112L55 98V216H110V98L122 112L132 70L110 40Z" fill="#667b92"/></svg>'})
      if(url.pathname==='/api/v1/account/generation-budget')return route.fulfill({json:{day:'2026-10-30',currency:'USD',dailyBudgetUsd:20,budgetUsedUsd:0,unpricedCount:0,prices:{}}})
      if(url.pathname==='/api/v1/cutflow-handoffs/sites')return route.fulfill({json:{defaultProfileId:null,sites:[]}})
      if(url.pathname==='/api/v1/library/kling/elements')return route.fulfill({json:{items:[],pageNum:1,pageSize:100,total:0,official:false}})
      if(url.pathname==='/api/v1/image-generation-batches')return route.fulfill({json:{items:[],hasMore:false,nextOffset:0}})
      if(fixtures.has(url.pathname))return route.fulfill({json:fixtures.get(url.pathname)})
      result.missingApis.push(url.pathname)
      return route.fulfill({status:404,json:{}})
    })
    await page.goto(origin+'/__task_pages_fixture?view=records',{waitUntil:'domcontentloaded'})
    await page.locator('.task-center-card').first().waitFor({timeout:15000})
    const count=await page.locator('.task-center-card').count()
    assert.equal(count,3,'one card for each of 3 tasks')
    assert.equal(await page.locator('.records-completed .result-card').count(),0)
    assert.equal(await page.locator('.records-overview').count(),0)
    await page.screenshot({path:resolve(reportDir,'records-'+width+'.png'),fullPage:true})
    await page.getByRole('button',{name:'查看任务详情：'+sourceTask.title,exact:true}).click()
    await page.locator('.task-flow-journey').waitFor()
    assert.equal(await page.getByRole('region',{name:'剪辑任务',exact:true}).count(),1)
    assert.equal(await page.getByRole('region',{name:'最终成片',exact:true}).count(),1)
    assert(await page.getByText('合成原片 01').count()>0)
    assert.equal(await page.locator('.task-cut-latest-label').count(),1)
    assert.equal(await page.locator('.task-cut-history').count(),2)
    assert.equal(await page.locator('.task-cut-history[open]').count(),0)
    assert.equal(await page.locator('.task-cut-entry:not(.is-archived)').count(),1)
    assert.equal(await page.locator('.task-cutflow-batches').count(),0)
    assert(await page.locator('.task-detail-single-history').count()===1)
    await page.screenshot({path:resolve(reportDir,'detail-'+width+'.png'),fullPage:true})
    await page.getByRole('button',{name:'关闭任务详情'}).click()
    await page.locator('.task-center-card').first().waitFor()
    result.tests.push({width,taskCards:count,detailLayout:true,roundTrip:true,viewportOverflow:await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)})
    await context.close()
  }
} finally {
  await browser.close()
  await server.close()
}
await writeFile(resolve(reportDir,'report.json'),JSON.stringify(result,null,2))
assert.equal(result.unexpectedWrites.length,0,'no writes')
assert.equal(result.externalRequests.length,0,'no network outside fixture')
assert.equal(result.errors.length,0,'no browser errors')
console.log(JSON.stringify(result,null,2))
