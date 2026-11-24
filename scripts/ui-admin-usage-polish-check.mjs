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
import {AdminUsagePanel} from '/src/features/admin/AdminUsagePanel.tsx';
import '/src/styles.css'; import '/src/features/admin/adminCenter.css';
const actor={userId:'root',username:'classic-admin',role:'ADMIN'};
createRoot(document.getElementById('root')).render(React.createElement('main',{className:'admin-view',style:{marginLeft:'390px',padding:'24px',minWidth:0}},React.createElement(AdminUsagePanel,{actor,users:[],brands:[]})));
</script></body></html>`
const totals = { recordCount: 10, imageCount: 2, videoSeconds: 60, cutflowClips: 4, originalDownloads: 3, cutflowDownloads: 2, unknownVideoCount: 0, estimatedUsd: 10, reservedUsd: 2, uncertainUsd: 1, releasedUsd: 0, unpricedCount: 0, budgetUsedUsd: 13 }
const requests = [], errors = []
const server = await createServer({ configFile: false, envFile: false, cacheDir: resolve(reportDir, 'vite-cache'), root: resolve(root, 'frontend'), server: { host: '127.0.0.1', port: 0 }, logLevel: 'error', plugins: [{ name: 'usage-polish', configureServer(vite) {
  vite.middlewares.use('/__usage_polish', async (_request, response, next) => { try { response.setHeader('Content-Type', 'text/html'); response.end(await vite.transformIndexHtml('/__usage_polish', html)) } catch (error) { next(error) } })
} }] })
let browser
try {
  await server.listen()
  const origin = 'http://127.0.0.1:' + server.httpServer.address().port
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1754, height: 1000 } })
  await page.clock.install({ time: new Date('2026-11-24T04:00:00Z') })
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) return route.abort()
    if (!url.pathname.startsWith('/api/')) return route.continue()
    assert.equal(route.request().method(), 'GET')
    const q = Object.fromEntries(url.searchParams); requests.push(q)
    const used = q.endDate === '2026-11-24' ? 13 : 7
    await route.fulfill({ json: { ...totals, day: q.endDate, startDate: q.startDate, endDate: q.endDate, budgetDay: '2026-11-24', currency: 'USD', total: 0, summaryTotal: 8, eventTotal: 0, policies: [], items: [], events: [], summaries: Array.from({length:8},(_,i)=>({...totals,userId:'a'+i,username:['GC','GK','IP','NW','PW','TT','TSG','Y2K'][i],brandId:null,isActive:true,dailyBudgetUsd:50,budgetUsedUsd:used,todayBudgetUsedUsd:13,todayUnpricedCount:0,todayRemainingUsd:37})) } })
  })
  await page.goto(origin + '/__usage_polish')
  const panel = page.getByRole('region', {name:'账号使用记录',exact:true})
  const table = panel.getByRole('region',{name:'账号用量汇总表'})
  await table.locator('tbody tr').nth(7).waitFor()
  assert.equal(await panel.getByRole('button',{name:'今天',exact:true}).getAttribute('aria-pressed'),'true')
  assert.equal(await panel.locator('.admin-usage-definitions').getAttribute('open'),null)
  assert.equal(await panel.locator('.admin-usage-explainer').count(),0)
  assert.equal(await panel.locator('.admin-usage-metrics').evaluate(el=>getComputedStyle(el).display),'grid')
  for (const width of [1754,1440,1280]) {
    await page.setViewportSize({width,height:1000})
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
    await page.screenshot({path:resolve(reportDir,'usage-'+width+'.png'),animations:'disabled'})
  }
  await page.setViewportSize({width:1754,height:1000})
  await panel.getByRole('button',{name:'自定义',exact:true}).click()
  const cal = panel.getByRole('group',{name:'日期范围日历'})
  await cal.waitFor()
  await page.screenshot({path:resolve(reportDir,'calendar.png'),animations:'disabled'})
  await cal.getByRole('button',{name:'2026-11-18',exact:true}).click()
  await table.getByText('US$43.00',{exact:true}).first().waitFor()
  await cal.getByRole('button',{name:'2026-11-20',exact:true}).click()
  await cal.waitFor({state:'hidden'})
  await table.getByText('US$43.00',{exact:true}).first().waitFor()
  assert(requests.some(q=>q.startDate==='2026-11-18'&&q.endDate==='2026-11-20'))
  assert(requests.some(q=>q.startDate==='2026-11-20'&&q.endDate==='2026-11-20'))
  await panel.getByRole('button',{name:'选择统计日期范围'}).click()
  const a = await cal.getByRole('button',{name:'2026-11-16',exact:true}).boundingBox()
  const b = await cal.getByRole('button',{name:'2026-11-19',exact:true}).boundingBox()
  await page.mouse.move(a.x+a.width/2,a.y+a.height/2)
  await page.mouse.down()
  await page.mouse.move(b.x+b.width/2,b.y+b.height/2,{steps:8})
  await page.mouse.up()
  await cal.waitFor({state:'hidden'})
  await table.getByText('US$43.00',{exact:true}).first().waitFor()
  assert(requests.some(q=>q.startDate==='2026-11-16'&&q.endDate==='2026-11-19'))
  await panel.getByRole('button',{name:'查看全部明细'}).click()
  await page.getByRole('dialog').waitFor()
  await page.screenshot({path:resolve(reportDir,'drawer.png'),animations:'disabled'})
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({state:'hidden'})
  await page.emulateMedia({reducedMotion:'reduce'})
  assert.equal(await panel.locator('.admin-usage-results').evaluate(el=>getComputedStyle(el).animationName),'none')
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({desktopWidths:[1754,1440,1280],dateClick:true,dateDrag:true,drawer:true,reducedMotion:true,requests:requests.length,errors}))
} finally {
  await browser?.close()
  await server.close()
}
