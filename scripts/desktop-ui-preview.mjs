import { createRequire } from 'node:module'
import { readFile, access } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'
const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const requireFrontend = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(requireFrontend.resolve('vite')).href)
const responses = createPageResponses()
responses.set('/api/v1/auth/me', {...responses.get('/api/v1/auth/me'), username: 'classic-admin'})
responses.set('/api/v1/account/generation-budget', {day: '2026-11-12', currency: 'USD', dailyBudgetUsd: 100, budgetUsedUsd: 0, unpricedCount: 0, prices: {imagePriceUsd: 0, videoPricePerSecondUsd: 0, depthVideoPricePerSecondUsd: 0, recreateVideoPricePerSecondUsd: 0, cutflowPriceUsd: 0}})
responses.set('/api/v1/image-generation-batches', {items: [], hasMore: false, nextOffset: 0})
responses.set('/api/v1/cutflow/multi-source-tasks', [])
let styleAttempts = 0
let theme = ''
try { await access(resolve(root, 'frontend/src/visualSystem.css')); theme = "import '/src/visualSystem.css';" } catch {}
const imageCheck = await readFile(resolve(root, 'scripts/ui-image-review-check.mjs'), 'utf8')
let imagesHtml = imageCheck.split('const html = '+String.fromCharCode(96))[1].split(String.fromCharCode(96))[0]
imagesHtml = imagesHtml.replace('[1,2].map', '[1,2,3,4].map').replace("import '/src/styles.css';", "import '/src/styles.css';" + theme)
const appHtml = '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">import React from "react"; import {createRoot} from "react-dom/client"; import App from "/src/app/App.tsx"; import "/src/styles.css"; '+theme+' createRoot(document.getElementById("root")).render(React.createElement(App));</script></body></html>'
const libraryHtml = appHtml.replace('import App from "/src/app/App.tsx";', 'import {ContentLibraryView as App} from "/src/features/library/ContentLibraryView.tsx";')
const server = await createServer({
 configFile: false, envFile: false, root: resolve(root, 'frontend'),
 cacheDir: resolve(root, 'tmp/desktop-ui-vite-cache'), logLevel: 'error',
 server: {host: '127.0.0.1', port: Number(process.env.DESKTOP_PREVIEW_PORT ?? 5189), strictPort: true},
 plugins: [{name: 'desktop-review-fixture', configureServer(vite) {
  vite.middlewares.use(async (req, res, next) => {
   const url = new URL(req.url, 'http://127.0.0.1')
   if (url.pathname === '/__images' || url.pathname === '/__app' || url.pathname === '/__library') {
    if (url.pathname === '/__library') styleAttempts = 0
    res.setHeader('Content-Type', 'text/html; charset=utf-8')
    res.end(await vite.transformIndexHtml(url.pathname, url.pathname === '/__images' ? imagesHtml : url.pathname === '/__library' ? libraryHtml : appHtml)); return
   }
   if (!url.pathname.startsWith('/api/')) return next()
   res.setHeader('Content-Type', 'application/json; charset=utf-8')
   if (req.method !== 'GET') { res.statusCode = 405; res.end(JSON.stringify({detail: 'Synthetic fixture blocks writes'})); return }
   if (url.pathname === '/api/v1/site-style-profiles' && styleAttempts++ === 0) {
    res.statusCode = 503; res.end(JSON.stringify({detail: '合成验收：风格库首次读取失败，请重试'})); return
   }
   if (url.pathname === '/api/v1/system/status') {
    res.end(JSON.stringify({...responses.get(url.pathname), imageGeneration: {available: false, reason: '合成验收：生成关闭'}})); return
   }
   if (url.pathname === '/api/v1/tasks' && !url.searchParams.has('cursor')) res.setHeader('X-Next-Cursor', 'synthetic-history')
   if (url.pathname === '/api/v1/tasks' && url.searchParams.has('cursor')) { res.end(JSON.stringify([{...responses.get('/api/v1/tasks')[2], id: 'synthetic-history', title: '历史目标任务', variants: [{...responses.get('/api/v1/tasks')[2].variants[0], id: 'synthetic-history-variant'}]}])); return }
   if (responses.has(url.pathname)) { res.end(JSON.stringify(responses.get(url.pathname))); return }
   res.statusCode = 404; res.end(JSON.stringify({detail: 'Synthetic fixture has no endpoint'}))
  })
 }}],
})
await server.listen()
console.log(JSON.stringify({origin: server.resolvedUrls.local[0], root, scope: 'desktop synthetic preview; all API writes blocked'}))
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => {await server.close(); process.exit(0)})
