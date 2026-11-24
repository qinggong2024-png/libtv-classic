import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_USAGE_REPORT_DIR)
const reportDir = resolve(process.env.UI_USAGE_REPORT_DIR)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
await mkdir(reportDir, { recursive: true })
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import {AdminCenterView} from '/src/features/admin/AdminCenterView.tsx'; import '/src/styles.css';
function Fixture(){const [section,setSection]=React.useState('usage');return React.createElement('main',{style:{marginLeft:'210px',padding:'20px',minWidth:0}},React.createElement(AdminCenterView,{actor:{userId:'root',username:'classic-admin',role:'ADMIN'},system:{sharedAuthentication:true,environment:'test'},section,onSectionChange:setSection,onKlingChanged:async()=>{}}))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture));
</script></body></html>`
const users = [{id:'a',username:'GC',brandId:'g',role:'USER',isActive:true,dailyBudgetUsd:50,isBrandAdmin:false,pagePermissions:[],createdAt:'2026-10-01T00:00:00Z'}]
const brands = [{id:'g',name:'GC / GuidingCross'}]
const totals = { recordCount: 1, imageCount: 0, videoSeconds: 60, cutflowClips: 2, originalDownloads: 1, cutflowDownloads: 1, unknownVideoCount: 0, estimatedUsd: 10, reservedUsd: 0, uncertainUsd: 0, releasedUsd: 0, unpricedCount: 0, budgetUsedUsd: 10 }
const events = Array.from({length:45},(_,i)=>({id:'event-'+i,username:'GC',action:i%2?'LOGIN':'CREATE_VIDEO_TASK',category:i%2?'SYSTEM':'VIDEO',quantity:i%2?null:2,detailTitle:i%2?null:'测试视频任务',createdAt:'2026-11-24T04:23:01Z',entityType:i%2?'user':'parent_task',entityId:i%2?'user-a':'task-a'}))
const requests = [], errors = []
const server = await createServer({ configFile:false, envFile:false, cacheDir:resolve(reportDir,'vite-cache'), root:resolve(root,'frontend'), server:{host:'127.0.0.1',port:0},logLevel:'error',plugins:[{name:'events-polish',configureServer(vite){
  vite.middlewares.use('/__events_polish',async (_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await vite.transformIndexHtml('/__events_polish',html))}catch(error){next(error)}})
}}] })
let browser
try {
  await server.listen()
  const origin = 'http://127.0.0.1:'+server.httpServer.address().port
  browser = await chromium.launch({channel:'chrome',headless:true})
  const page = await browser.newPage({viewport:{width:1754,height:1000},timezoneId:'America/Los_Angeles'})
  await page.clock.install({time:new Date('2026-11-24T04:30:00Z')})
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/*',async route=>{
    const url = new URL(route.request().url())
    if(url.origin!==origin)return route.abort()
    if(!url.pathname.startsWith('/api/'))return route.continue()
    assert.equal(route.request().method(),'GET')
    const q = Object.fromEntries(url.searchParams);requests.push({path:url.pathname,...q})
    if(url.pathname==='/api/v1/admin/users')return route.fulfill({json:users})
    if(url.pathname==='/api/v1/admin/brands')return route.fulfill({json:brands})
    if(url.pathname==='/api/v1/admin/account-usage')return route.fulfill({json:{...totals,day:q.endDate,startDate:q.startDate,endDate:q.endDate,budgetDay:'2026-11-24',currency:'USD',total:0,summaryTotal:1,eventTotal:0,policies:[],items:[],events:[],summaries:[{...totals,userId:'a',username:'GC',brandId:'g',isActive:true,dailyBudgetUsd:50,todayBudgetUsedUsd:10,todayUnpricedCount:0,todayRemainingUsd:40}]}})
    if(url.pathname==='/api/v1/account/events'){
      assert.equal(q.view,'production')
      const items=events.filter(e=>(!q.category||e.category===q.category)&&(!q.endDate||q.endDate>='2026-11-24'))
      const offset=Number(q.offset||0),limit=Number(q.limit||20)
      return route.fulfill({json:{view:'production',startDate:q.startDate,endDate:q.endDate,total:items.length,items:items.slice(offset,offset+limit)}})
    }
    errors.push('Unmocked API '+url.pathname)
    return route.fulfill({status:404,json:{}})
  })
  await page.goto(origin+'/__events_polish')
  const usage=page.getByRole('region',{name:'账号使用记录',exact:true}), log=page.getByRole('region',{name:'账号操作记录',exact:true})
  await usage.locator('tbody tr').first().waitFor()
  const styles=async panel=>panel.evaluate(el=>{
    const css=(selector,prop)=>getComputedStyle(selector?el.querySelector(selector):el)[prop]
    return [css('', 'borderRadius'),css('','padding'),css('.admin-usage-periods','borderRadius'),css('.admin-usage-periods','display'),css('.admin-usage-refresh','height'),css('.usage-table-scroll','borderRadius')]
  })
  const reference=await styles(usage)
  await page.waitForTimeout(350)
  const position=await usage.boundingBox()
  await page.getByRole('button',{name:'操作记录',exact:true}).click()
  const table=log.getByRole('region',{name:'操作记录表'})
  await table.locator('tbody tr').nth(19).waitFor()
  assert.deepEqual(await styles(log),reference)
  await page.waitForTimeout(350)
  const actual=await log.boundingBox()
  assert(Math.abs(actual.x-position.x)<1, 'report panel horizontal position must match after transition')
  assert(Math.abs(actual.y-position.y)<1, 'report panel vertical position must match after transition')
  assert((await table.locator('tbody time').first().innerText()).includes('12:23:01'))
  for(const width of [1754,1440,1280]){
    await page.setViewportSize({width,height:1000})
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
    await page.screenshot({path:resolve(reportDir,'events-'+width+'.png'),animations:'disabled'})
  }
  await log.getByRole('button',{name:'操作记录下一页'}).click()
  await log.getByText('第 2 / 3 页 · 共 45 条',{exact:true}).waitFor()
  await log.getByLabel('操作类型',{exact:true}).selectOption('SYSTEM')
  await log.getByText('第 1 / 2 页 · 共 22 条',{exact:true}).waitFor()
  assert.equal(await table.getByRole('columnheader',{name:'明细',exact:true}).count(),0)
  assert.equal(await table.getByRole('button',{name:'查看操作详情'}).count(),0)
  await log.getByLabel('操作类型',{exact:true}).selectOption('VIDEO')
  await log.getByText('第 1 / 2 页 · 共 23 条',{exact:true}).waitFor()
  await table.getByText('创建生视频任务 2 条',{exact:true}).first().waitFor()
  await table.getByRole('button',{name:'查看操作详情'}).first().click()
  const drawer=page.getByRole('dialog')
  await drawer.getByText('task-a',{exact:true}).waitFor()
  await drawer.getByText('测试视频任务',{exact:true}).waitFor()
  await page.screenshot({path:resolve(reportDir,'event-detail.png'),animations:'disabled'})
  await page.keyboard.press('Escape')
  await drawer.waitFor({state:'hidden'})
  await log.getByRole('button',{name:'自定义',exact:true}).click()
  const cal=log.getByRole('group',{name:'日期范围日历'})
  await cal.waitFor()
  const a=await cal.getByRole('button',{name:'2026-11-18',exact:true}).boundingBox()
  const b=await cal.getByRole('button',{name:'2026-11-20',exact:true}).boundingBox()
  await page.mouse.move(a.x+a.width/2,a.y+a.height/2);await page.mouse.down()
  await page.mouse.move(b.x+b.width/2,b.y+b.height/2,{steps:8});await page.mouse.up()
  await cal.waitFor({state:'hidden'})
  await log.getByText('所选条件下没有操作记录。',{exact:true}).waitFor()
  assert(requests.some(q=>q.path==='/api/v1/account/events'&&q.startDate==='2026-11-18'&&q.endDate==='2026-11-20'))
  await page.setViewportSize({width:1754,height:1000})
  await page.getByRole('button',{name:'用量与费用',exact:true}).click()
  await usage.locator('tbody tr').first().waitFor()
  assert.deepEqual(await styles(usage),reference)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({sharedStyles:reference,samePanelPosition:true,widths:[1754,1440,1280],dateDrag:true,pagination:true,filter:true,drawer:true,shanghaiTime:true,errors}))
}finally{await browser?.close();await server.close()}
