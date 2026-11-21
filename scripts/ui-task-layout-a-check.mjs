import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { access, mkdir, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_LAYOUT_A_REPORT_DIR, 'Set Playwright and external report directory')
const output = resolve(process.env.UI_LAYOUT_A_REPORT_DIR)
assert(!output.startsWith(root), 'Keep generated evidence outside the worktree')
await mkdir(output, { recursive: true })
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const videoPath = resolve(output, 'synthetic.mp4')
execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x48635b:s=180x320:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', videoPath])
const video = await readFile(videoPath)
let theme = ''
try { await access(resolve(root, 'frontend/src/visualSystem.css')); theme = "import '/src/visualSystem.css';" } catch { /* Classic testing palette. */ }
const base = createPageResponses().get('/api/v1/tasks')[2]
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React,{useState} from 'react';import{createRoot}from'react-dom/client';import{ProductionWorkspace}from'/src/features/production/ProductionWorkspace.tsx';import'/src/styles.css';${theme}
const base=${JSON.stringify(base)};const scenario=new URL(location.href).searchParams.get('state')||'ready';
const generated=scenario!=='generating';const variants=Array.from({length:Number(new URL(location.href).searchParams.get('count')||2)},(_,i)=>i).map(i=>({...base.variants[0],id:'layout-variant-'+i,kind:i?'DYNAMIC':'STABLE',promptText:'原始提示词保留测试',downloadArtifactId:generated?'layout-artifact-'+i:null,generationStatus:generated?'SUCCEEDED':'RUNNING',downloadStatus:generated?'READY':'PENDING'}));
const task={...base,phase:generated?'DELIVERABLE':'GENERATING',title:'服装广告视频制作 · 任务详情 A',id:'layout-task',variants,settings:{...base.settings,durationSeconds:5,generationMode:'custom',autoSubmitToCutflow:false}};
const batch={id:'layout-batch',status:'COMPLETED',updatedAt:'2026-11-13T03:00:00Z',sources:variants.map((v,i)=>({clipId:'clip-'+i,order:i+1,taskId:task.id,variantId:v.id,artifactId:v.downloadArtifactId,title:'原片 '+(i+1),durationSeconds:5})),outputs:[{id:'layout-output',mediaUrl:'/api/v1/layout-video',width:180,height:320,durationSeconds:10,viewedAt:null,reviewStatus:'PENDING'}]};
function Fixture(){const[selected,setSelected]=useState([]);const batches=scenario==='ready'?[batch]:scenario==='failed'?[{...batch,status:'FAILED',outputs:[],errorMessage:'模拟剪辑失败：媒体校验未通过'}]:[];const basket={selection:selected,entries:[],canSubmit:selected.length>0,submitting:false,submitHint:null,maxSources:30,atCapacity:false,multiTasksLoaded:true,multiTaskError:null,refreshMultiTasks:()=>{},isSelected:id=>selected.includes(id),isInProgress:()=>false,isPreviouslyCut:()=>false,relatedMultiSourceTasksForTask:()=>batches,singleSourceVersionsFor:()=>[],toggle:id=>setSelected(s=>s.includes(id)?s.filter(x=>x!==id):[...s,id]),submit:async()=>{window.layoutSubmissions=(window.layoutSubmissions||0)+1},submitOne:async()=>{window.layoutSubmissions=(window.layoutSubmissions||0)+1}};return React.createElement('div',{className:'app-shell'},React.createElement('aside',{style:{width:196,padding:24,flexShrink:0,borderRight:'1px solid #e0e6ef'}},React.createElement('img',{src:'/brand/allwe-logo.png',style:{width:112}}),React.createElement('p',null,'衣影工坊 · 经典版'),React.createElement('p',{style:{marginTop:48}},'图片中心'),React.createElement('p',null,'视频制作'),React.createElement('p',null,'任务')),React.createElement('main',{style:{flex:1,minWidth:0,padding:24}},React.createElement(ProductionWorkspace,{task,sharedBasket:basket,multiCutReady:true,onClose:()=>{},onChanged:async()=>{},onRemix:()=>{},onReuse:()=>{},onPlay:()=>{window.layoutPlayed=true}})))}createRoot(document.getElementById('root')).render(React.createElement(Fixture));</script></body></html>`
const server = await createServer({configFile:false,envFile:false,root:resolve(root,'frontend'),cacheDir:resolve(output,'vite-cache'),logLevel:'error',optimizeDeps:{entries:[]},server:{host:'127.0.0.1',port:0},plugins:[{name:'task-layout-a-fixture',configureServer(vite){vite.middlewares.use(async(req,res,next)=>{if(req.url.startsWith('/__layout')){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(await vite.transformIndexHtml('/__layout',html));return}next()})}}]})
const report={checks:[],errors:[],writes:[]}
let browser
try {
  await server.listen();const origin=`http://127.0.0.1:${server.httpServer.address().port}`
  browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROME_PATH?{executablePath:process.env.PLAYWRIGHT_CHROME_PATH}:{channel:'chrome'})})
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'})
  page.setDefaultTimeout(12000);page.on('pageerror',e=>report.errors.push(e.message))
  await page.route('**/api/**',async route=>{const r=route.request();if(r.method()!=='GET'){report.writes.push(r.url());return route.abort()}if(r.url().includes('thumbnail')||r.url().endsWith('.png'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="180" height="320"><rect width="180" height="320" fill="#48635b"/></svg>'});if(r.url().includes('layout-video'))return route.fulfill({contentType:'video/mp4',body:video});return route.fulfill({json:{enabled:true,siteRequired:false,handoffs:[]}})})
  for(const state of ['ready','pending','generating','failed']){
    await page.goto(origin+'/__layout?state='+state);await page.locator('.task-layout-a').waitFor()
    for(const width of [1280,1440,1920,2560]){await page.setViewportSize({width,height:1000});assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),state+' overflow '+width)
      if(state==='ready'){const result=await page.locator('.task-a-delivery__preview').boundingBox();const source=await page.locator('.task-a-delivery__sources').boundingBox();assert(source.x>=result.x+result.width);assert(await page.locator('.task-a-delivery__sources').isVisible());const panel=await page.locator('.task-a-delivery').boundingBox();const container=await page.locator('.drawer-content').boundingBox();assert(panel.width>container.width*.9,'latest output must fill the content column')}if(state==='ready'&&width===1920)await page.screenshot({path:resolve(output,'ready-1920.png'),fullPage:true})
    }
    await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:resolve(output,state+'-1440.png'),fullPage:true})
    if(state==='ready'){
      const params=page.locator('.task-a-disclosure').filter({has:page.locator('summary strong',{hasText:'生成参数'})}).first();assert(!await params.evaluate(e=>e.open));await params.locator('summary').focus();await page.keyboard.press('Enter');assert(await params.evaluate(e=>e.open));assert((await params.innerText()).includes('原始提示词保留测试'));await page.keyboard.press('Enter')
      await page.getByRole('button',{name:'查看剪辑记录',exact:true}).click();assert(await page.locator('#task-cutflow-records').evaluate(e=>e.open));
      await page.getByRole('button',{name:'预览剪辑成片',exact:true}).click();await page.locator('[role="dialog"]').waitFor();await page.keyboard.press('Escape');await page.locator('[role="dialog"]').waitFor({state:'detached'})
    }
    if(state==='failed'){await page.locator('#task-cutflow-records > summary').click();assert(await page.locator('#task-cutflow-records').getByRole('alert').filter({hasText:'模拟剪辑失败：媒体校验未通过'}).isVisible())}
    if(state==='pending'){const toggle=page.locator('.cutflow-source-toggle input').first();await page.locator('.cutflow-source-toggle').first().click();assert(await toggle.isChecked());const submit=page.locator('.task-original-gallery__submit');assert(await submit.isEnabled());await submit.click();assert.equal(await page.evaluate(()=>window.layoutSubmissions),1)}
    report.checks.push(state+': desktop widths, actual components, primary content and available actions')
  }
  await page.goto(origin+'/__layout?state=ready&count=30');await page.locator('.task-a-delivery').waitFor();const list=page.locator('.task-a-delivery__sources ol');assert.equal(await list.locator('li').count(),30);assert(await list.evaluate(e=>e.clientHeight<=292&&e.scrollHeight>e.clientHeight));report.checks.push('30 sources: visible scrollable trace with bounded height')
  await page.goto(origin+'/__layout?state=pending&count=1');await page.locator('#task-original-clips').waitFor();assert(await page.locator('#task-original-clips').isVisible());assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth));await page.screenshot({path:resolve(output,'single-pending-1440.png'),fullPage:true});report.checks.push('single original: visible and no overflow')
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.writes,[]);console.log(JSON.stringify(report))
} finally {await writeFile(resolve(output,'report.json'),JSON.stringify(report,null,2));await browser?.close();await server.close()}
