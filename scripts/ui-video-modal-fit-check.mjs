import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { access, mkdir, readFile, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_VIDEO_FIT_REPORT_DIR, 'Set Playwright and an external report directory')
const output = resolve(process.env.UI_VIDEO_FIT_REPORT_DIR)
assert(relative(root, output).startsWith('..') || isAbsolute(relative(root, output)), 'Keep evidence outside the worktree')
await mkdir(output, { recursive: true })
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
for (const [name, size] of [['portrait', '180x320'], ['landscape', '320x180']]) {
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', `color=c=0x48635b:s=${size}:d=1`, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', resolve(output, `${name}.mp4`)])
}
let theme = ''
try { await access(resolve(root, 'frontend/src/visualSystem.css')); theme = "import '/src/visualSystem.css';" } catch { /* Testing uses its own classic palette. */ }
const title = 'Woman_smiling_at_Christmas_party_4e119493_2K · 11/13 12:14 · 结果 1'
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React,{useState}from'react';import{createRoot}from'react-dom/client';import{VideoModal}from'/src/shared/components/Media.tsx';import'/src/styles.css';${theme}
function Fixture(){const[props,setProps]=useState({title:${JSON.stringify(title)}});window.setPreview=setProps;return React.createElement(VideoModal,{...props,onClose:()=>window.previewClosed=true})}createRoot(document.getElementById('root')).render(React.createElement(Fixture));</script></body></html>`
const server = await createServer({configFile:false,envFile:false,root:resolve(root,'frontend'),cacheDir:resolve(output,'vite-cache'),logLevel:'error',optimizeDeps:{entries:[]},server:{host:'127.0.0.1',port:0},plugins:[{name:'video-fit-fixture',configureServer(vite){vite.middlewares.use('/__video_fit',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(await vite.transformIndexHtml('/__video_fit',html))}catch(error){next(error)}})}}]})
const report = { checks: [], errors: [] }
let browser
try {
  await server.listen()
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ reducedMotion: 'reduce' })
  page.on('pageerror', error => report.errors.push(error.message))
  await page.route('**/fixture-video/*.mp4', async route => route.fulfill({ contentType: 'video/mp4', body: await readFile(resolve(output, new URL(route.request().url()).pathname.split('/').at(-1))) }))
  for (const viewport of [{width:1280,height:975},{width:1440,height:900},{width:1920,height:1080},{width:2560,height:1440}]) {
    await page.setViewportSize(viewport)
    await page.goto(`${origin}/__video_fit`)
    await page.locator('[role="dialog"]').waitFor()
    const check = async name => {
      const bounds = await page.evaluate(() => {
        const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom} }
        return {dialog:rect('[role="dialog"]'),stage:rect('.video-stage'),header:rect('.video-modal header'),close:rect('.video-modal button'),title:document.querySelector('h2').textContent,ellipsis:getComputedStyle(document.querySelector('h2')).textOverflow}
      })
      assert(Math.abs(bounds.dialog.width - bounds.stage.width) < 2, `${name}: title created spare width: ${JSON.stringify(bounds)}`)
      assert(bounds.dialog.x >= 0 && bounds.dialog.right <= viewport.width + 1 && bounds.dialog.y >= 0 && bounds.dialog.bottom <= viewport.height + 1, `${name}: dialog outside viewport`)
      assert(bounds.close.right <= bounds.dialog.right + 1, `${name}: close button clipped`)
      assert.equal(bounds.title, title)
      assert.equal(bounds.ellipsis, 'ellipsis')
      report.checks.push({viewport,name,...bounds})
    }
    await check('loading')
    for (const media of ['portrait','landscape']) {
      await page.evaluate(({src,title})=>window.setPreview({src,title}),{src:`/fixture-video/${media}.mp4`,title})
      await page.waitForFunction(()=>document.querySelector('video')?.videoWidth > 0)
      await page.waitForFunction(()=>{const video=document.querySelector('video');return video.videoHeight>0 && Math.abs(parseFloat(document.querySelector('.video-stage').style.aspectRatio)-video.videoWidth/video.videoHeight)<0.001})
      await page.waitForFunction(()=>{const video=document.querySelector('video');const expected=Math.min((innerHeight-110)*video.videoWidth/video.videoHeight,innerWidth-32,1080);return Math.abs(document.querySelector('.video-stage').getBoundingClientRect().width-expected)<1})
      await check(media)
      await page.screenshot({path:resolve(output,`${viewport.width}x${viewport.height}-${media}.png`)})
    }
    await page.evaluate(title=>window.setPreview({title,loadError:'视频加载失败，请稍后重试'}),title)
    await page.locator('.video-error').waitFor()
    await check('error')
    await page.getByRole('button',{name:'关闭预览',exact:true}).click()
    assert(await page.evaluate(()=>window.previewClosed))
  }
  assert.deepEqual(report.errors,[])
  console.log(JSON.stringify({checks:report.checks.length,errors:report.errors}))
} finally {
  await writeFile(resolve(output,'report.json'),JSON.stringify(report,null,2))
  await browser?.close()
  await server.close()
}
