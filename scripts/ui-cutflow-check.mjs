import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { resolve, relative, isAbsolute } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

assert(process.env.PLAYWRIGHT_MODULE && process.env.UI_CUTFLOW_REPORT_DIR, 'Set Playwright and external report directory')
const reportDir = resolve(process.env.UI_CUTFLOW_REPORT_DIR)
await mkdir(reportDir, { recursive: true })
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const { expect } = createRequire(process.env.PLAYWRIGHT_MODULE)('playwright/test')
const browser = await chromium.launch({ channel: process.env.UI_BROWSER_CHANNEL || 'chrome', headless: true })
const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
const reportRelative = relative(root, reportDir)
assert(reportRelative.startsWith('..') || isAbsolute(reportRelative), 'Use external evidence directory')
const frontendRequire = createRequire(resolve(root, 'frontend/package.json'))
const { createServer } = await import(pathToFileURL(frontendRequire.resolve('vite')).href)
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client'; import App from '/src/app/App.tsx'; import '/src/styles.css';
createRoot(document.getElementById('root')).render(React.createElement(App));</script></body></html>`
const server = await createServer({ configFile: false, envFile: false, root: resolve(root, 'frontend'),
  cacheDir: resolve(reportDir, 'vite-cache'), logLevel: 'error', server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'cutflow-fixture', configureServer(vite) {
    vite.middlewares.use('/__cutflow_fixture', async (_req, res, next) => {
      try { res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/__cutflow_fixture', html)) }
      catch (error) { next(error) }
    })
  } }],
})
await server.listen()
const fixture = { server, origin: `http://127.0.0.1:${server.httpServer.address().port}` }
const fixtures = createPageResponses()
const tasks = fixtures.get('/api/v1/tasks').filter(task => task.phase === 'DELIVERABLE').slice(0, 1)
const videoPath = resolve(reportDir, 'test-video.mp4')
execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x288:rate=10', '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', videoPath], { windowsHide: true })
const media = await readFile(videoPath)
const report = { scope: 'isolated-fixture-no-production-writes', browser: browser.version(), cases: [], errors: [], consoleErrors: [], externalRequests: [] }
let activePage
try {
  const widths = process.env.UI_CUTFLOW_WIDTHS ? process.env.UI_CUTFLOW_WIDTHS.split(',').map(Number) : [1440, 1024, 768, 390]
  assert(widths.length && widths.every(width => [1440, 1024, 768, 390].includes(width)), 'Use supported verification widths')
  for (const width of widths) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, serviceWorkers: 'block', reducedMotion: 'reduce' })
    const page = await context.newPage()
    activePage = page
    page.on('pageerror', error => report.errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text()) })
    let enabled = false
    let role = 'ADMIN'
    let recordsAccess = true
    let handoffs = []
    let submissions = 0
    let retries = 0
    let readFailed = false
    let mediaFailed = false
    let loseResponse = false
    let sourceBound = false
    let siteRequired = true
    let bindings = 0
    let defaultProfileId = null
    let diagnosticReads = 0
    let diagnosticFailure = false
    const sourceTask = { ...tasks[0], siteProfileId: null }
    const output = { id: 'render-1', sha256: 'a'.repeat(64), reviewStatus: 'PENDING', durationSeconds: 1,
      mediaUrl: '/api/v1/cutflow-handoffs/test-handoff/outputs/render-1', createdAt: new Date().toISOString() }
    await context.addCookies([{ name: 'synthetic_csrf', value: 'fixture-csrf', url: fixture.origin }])
    await context.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== fixture.origin) { report.externalRequests.push(request.url()); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      if (url.pathname === '/api/v1/system/status') {
        return route.fulfill({ json: fixtures.get(url.pathname) })
      }
      if (url.pathname === '/api/v1/video-tasks/deep/status') {
        return route.fulfill({ json: { configured: false, enabled: false, reason: '隔离验收不启用生成' } })
      }
      if (url.pathname === '/api/v1/auth/me') {
        return route.fulfill({ json: { ...fixtures.get(url.pathname), role, pagePermissions: !recordsAccess ? ['make'] : role === 'AUDITOR' ? ['records'] : ['make', 'records'] } })
      }
      if (url.pathname === '/api/v1/fixture-media/product.png' || url.pathname.endsWith('/thumbnail')) {
        return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="288"><rect width="160" height="288" fill="#c8d9cb"/><path d="M55 45L35 70L45 105L58 95V205H105V95L116 105L130 70L108 45Z" fill="#fff"/></svg>' })
      }
      if (url.pathname === '/api/v1/cutflow-handoffs' && request.method() === 'GET') {
        return readFailed ? route.fulfill({ status: 503, json: { detail: '隔离测试：状态服务暂不可用' } })
          : route.fulfill({ json: { enabled, siteRequired, handoffs } })
      }
      if (url.pathname === '/api/v1/cutflow-handoffs/multi' && request.method() === 'GET') {
        return route.fulfill({ json: { tasks: [] } })
      }
      if (url.pathname === '/api/v1/cutflow-handoffs/sites') {
        return route.fulfill({ json: { defaultProfileId, sites: [
          { id: 'pagewings', version: '1.2.0', name: '泛读书 / PageWings', siteUrl: 'https://www.pagewings.com/' },
          { id: 'guidingcross', version: '1.0.0', name: 'GuidingCross', siteUrl: 'https://www.guidingcross.com/' },
        ] } })
      }
      if (url.pathname.endsWith('/diagnostics')) {
        diagnosticReads++
        return diagnosticFailure ? route.fulfill({ status: 503, json: {} }) : route.fulfill({ json: {
          checkedAt: new Date().toISOString(), handoffKey: 'handoff-isolated', batchId: 'isolated-batch',
          localStatus: handoffs[0]?.status, sender: { state: 'online', heartbeatAt: new Date().toISOString() },
          remoteStage: 'render', lastContactAt: new Date().toISOString(), lastProgressAt: new Date().toISOString(),
          diagnosticsState: 'unavailable', workerAlive: null, scopeMatches: null,
          retryCount: 0, nextRetryAt: null, errorCode: null,
        } })
      }
      if (url.pathname === '/api/v1/cutflow-handoffs/source-site') {
        assert.equal(request.headers()['x-csrf-token'], 'fixture-csrf')
        assert.equal(request.postDataJSON().siteProfileId, 'pagewings')
        bindings++
        sourceBound = true
        return route.fulfill({ json: { profileId: 'pagewings', siteId: '158b00a8-6166-5623-b977-e32003b3dc68' } })
      }
      if (url.pathname === '/api/v1/cutflow-handoffs' && request.method() === 'POST') {
        submissions++
        assert.equal(request.headers()['x-csrf-token'], 'fixture-csrf')
        const input = request.postDataJSON()
        assert(input.taskId && input.variantId)
        handoffs = [{ id: 'test-handoff', handoffKey: 'handoff-isolated', sourceArtifactId: 'synthetic-artifact',
          status: 'QUEUED', cutflowBatchId: null, cutflowEditorUrl: null, errorMessage: null, outputs: [] }]
        if (loseResponse) return route.abort('failed')
        return route.fulfill({ status: 202, json: handoffs[0] })
      }
      if (url.pathname === '/api/v1/cutflow-handoffs/test-handoff/retry') {
        retries++
        handoffs[0].status = 'QUEUED'; handoffs[0].errorMessage = null
        return route.fulfill({ json: handoffs[0] })
      }
      if (url.pathname === output.mediaUrl) {
        return mediaFailed ? route.fulfill({ status: 410, json: { detail: '隔离测试：成片已过期' } })
          : route.fulfill({ contentType: 'video/mp4', body: media })
      }
      if (request.method() !== 'GET') assert.fail('Unexpected write: ' + url.pathname)
      if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: [{ ...sourceTask, siteProfileId: sourceBound ? 'pagewings' : null }] })
      if (url.pathname === '/api/v1/library/kling/elements') return route.fulfill({ json: { items: [], pageNum: 1, pageSize: 100, total: 0, official: false } })
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-16', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0, unpricedCount: 0, prices: {} } })
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      report.errors.push('Unmocked API: ' + url.pathname)
      return route.fulfill({ status: 404, json: {} })
    })
    await page.goto(fixture.origin + '/__cutflow_fixture?view=records')
    await page.locator('.records-completed .result-card').first().waitFor()
    await expect(page.getByRole('region', { name: '剪辑成片', exact: true })).toHaveCount(0)
    await expect(page.locator('.records-completed .result-card').first()).toContainText('生成原片')
    await expect(page.locator('.records-completed .result-card').first().getByRole('button', { name: '剪辑与成片', exact: true })).toBeVisible()
    enabled = true
    const nav = page.getByRole('navigation', { name: width <= 768 ? '移动端主导航' : '主导航', exact: true })
    await expect(nav.getByRole('button', { name: '剪辑与成片', exact: true })).toHaveCount(0)
    await page.locator('.records-completed .result-card').first().getByRole('button', { name: '剪辑与成片', exact: true }).click()
    await expect(page.getByRole('region', { name: '剪辑成片', exact: true })).toBeVisible()
    assert.equal(new URL(page.url()).searchParams.get('task'), sourceTask.id)
    const sourceVariant = sourceTask.variants.find(variant => variant.downloadArtifactId)
    assert.equal(new URL(page.url()).searchParams.get('variant'), sourceVariant.id)
    await page.reload()
    await expect(page.getByRole('region', { name: '剪辑成片', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '关闭任务详情', exact: true }).click()
    assert.equal(new URL(page.url()).searchParams.get('view'), 'records')
    await page.goto(fixture.origin + '/__cutflow_fixture?view=records&filter=cutflow-ready')
    await expect(page.locator('.records-completed .result-card').first()).toBeVisible()
    await page.goto(fixture.origin + '/__cutflow_fixture?view=cutflow')
    await expect(page.locator('.records-completed .result-card').first()).toBeVisible()
    const reloadEditing = async () => {
      await page.goto(fixture.origin + '/__cutflow_fixture?view=records&filter=done&q=' + encodeURIComponent(sourceTask.title))
      await page.locator('.records-completed .result-card').first().getByRole('button', { name: '剪辑与成片', exact: true }).click()
      await expect(page.getByRole('region', { name: '剪辑成片', exact: true })).toBeVisible()
      await expect(page.locator('.page-loading')).toHaveCount(0)
    }
    await reloadEditing()
    const control = page.getByRole('region', { name: '剪辑成片', exact: true }).first()
    const refresh = async () => {
      await control.getByRole('button', { name: '刷新剪辑状态' }).click()
      await expect(control.getByRole('button', { name: '刷新剪辑状态' })).toBeEnabled()
    }
    const timeline = page.locator('.task-timing-stage--cutflow')
    await expect(timeline).toContainText('待送剪辑')
    await expect(control.getByRole('progressbar', { name: '剪辑进度' })).toHaveCount(0)
    const send = control.getByRole('button', { name: /^送入剪辑/ })
    // LAN direct mode sends an existing task without a storefront/mapping step.
    siteRequired = false
    await reloadEditing()
    await expect(send).toBeEnabled()
    await expect(control.getByLabel('商品所属品牌')).toHaveCount(0)
    await send.evaluate(button => { button.click(); button.click() })
    await expect(timeline).toContainText('等待传输')
    assert.equal(submissions, 1)
    assert.equal(bindings, 0)
    handoffs = []; submissions = 0; siteRequired = true
    await reloadEditing()
    await expect(send).toBeDisabled()
    const siteSelect = control.getByLabel('商品所属品牌')
    await expect(siteSelect).toHaveValue('')
    await siteSelect.selectOption('pagewings')
    await control.getByRole('button', { name: '保存站点关联' }).click()
    await expect(send).toBeEnabled()
    assert.equal(bindings, 1)
    await reloadEditing()
    await expect(send).toBeEnabled()
    await expect(control.getByLabel('商品所属品牌')).toHaveCount(0)
    await send.evaluate(button => { button.click(); button.click() })
    await expect(timeline).toContainText('等待传输')
    assert.equal(submissions, 1)
    assert.equal(await send.count(), 0)
    await reloadEditing()
    await expect(timeline).toContainText('等待传输')
    assert.equal(submissions, 1)

    for (const [status, label] of [['READY', '素材已准备，等待导入'], ['IMPORTING', '剪辑端导入中'], ['AWAITING_EDIT', '等待剪辑配置'], ['EDITING', '正在剪辑'], ['PENDING_REVIEW', '等待成片回传']]) {
      handoffs[0].status = status
      await refresh()
      await expect(timeline).toContainText(label)
      await expect(timeline).toHaveAttribute('aria-current', 'step')
    }
    await expect(timeline).toContainText('等待成片回传')
    // Automatic polling must update the UI without manual refresh or reload.
    handoffs[0].status = 'EDITING'
    await expect(timeline).toContainText('正在剪辑', { timeout: 12000 })
    await expect(timeline).toContainText('正在剪辑')
    await expect(page.locator('#task-timing-title')).toHaveText('正在剪辑')
    await page.screenshot({ path: resolve(reportDir, 'progress-' + width + '.png'), fullPage: true })
    readFailed = true
    await refresh()
    await control.getByRole('alert').filter({ hasText: '网站或生成服务暂时没有正常回应' }).waitFor()
    await expect(timeline).toContainText('正在剪辑')
    readFailed = false
    handoffs[0].status = 'FAILED'; handoffs[0].errorMessage = '隔离测试：NAS 断线'
    await refresh()
    await control.getByRole('button', { name: /^重试送入剪辑/ }).click()
    await expect(timeline).toContainText('等待传输')
    assert.equal(retries, 1)

    handoffs[0].status = 'PENDING_REVIEW'
    handoffs[0].cutflowEditorUrl = 'http://cutflow.invalid/'
    handoffs[0].outputs = [output]
    // A previous artifact must never leak into this result card.
    handoffs.push({ ...handoffs[0], id: 'other-source', sourceArtifactId: 'old-artifact', outputs: [{ ...output, id: 'old-render' }] })
    await refresh()
    await expect(control.getByRole('progressbar', { name: '剪辑进度' })).toHaveCount(0)
    await expect(timeline).toContainText('剪辑完成')
    assert.equal(await control.getByRole('button', { name: /预览剪辑成片/ }).count(), 1)
    assert.equal(await control.getByRole('link', { name: '打开剪辑工作台' }).getAttribute('rel'), 'noreferrer')
    handoffs[0].editMode = 'automatic'
    handoffs[0].status = 'AWAITING_EDIT'
    await refresh()
    await expect(timeline).toContainText('等待自动剪辑')
    await expect(control.getByRole('heading')).toHaveCount(0)
    await expect(control.getByText('剪辑成片 1', { exact: true })).toBeVisible()
    await expect(control.getByText('剪辑任务', { exact: true })).toHaveCount(0)
    assert.equal(await control.getByRole('link', { name: '打开剪辑工作台' }).count(), 0)
    handoffs[0].status = 'PENDING_REVIEW'
    await refresh()
    await expect(timeline).toContainText('剪辑完成')
    await reloadEditing()
    await expect(page.locator('.production-video-result h3').first()).toContainText('原片与剪辑')
    await expect(timeline).toContainText('剪辑完成')
    await expect(page.getByRole('region', { name: '剪辑成片', exact: true })).toContainText('剪辑成片 1')
    const thumbnail = control.getByRole('button', { name: '播放剪辑成片 1', exact: true })
    await expect(thumbnail.getByRole('img', { name: '剪辑成片 1封面', exact: true })).toBeVisible()
    await page.waitForFunction(() => {
      const video = document.querySelector('.cutflow-video-thumbnail video')
      return video?.readyState >= 2 && video.videoWidth > 0 && video.paused && !video.autoplay
    })
    assert.equal(await thumbnail.locator('video').getAttribute('src'), output.mediaUrl)
    const frameSizes = await page.locator('.production-video-result .variant-result-preview').evaluateAll(elements => elements.map(element => {
      const rect = element.getBoundingClientRect()
      return [rect.width, rect.height, getComputedStyle(element).borderRadius]
    }))
    assert.equal(frameSizes.length, 2)
    assert.deepEqual(frameSizes[0], frameSizes[1])
    await thumbnail.click()
    await expect(page.getByRole('dialog').locator('video')).toBeVisible()
    await page.getByRole('dialog').getByRole('button', { name: '关闭预览' }).focus()
    await page.keyboard.press('Escape')
    await page.getByRole('dialog').waitFor({ state: 'detached' })
    await expect(thumbnail).toBeFocused()
    await page.screenshot({ path: resolve(reportDir, 'cutflow-' + width + '.png'), fullPage: true })
    assert.equal(diagnosticReads, 0)
    await control.locator('summary').filter({ hasText: '链路状态' }).click()
    await control.getByText('渲染成片', { exact: true }).waitFor()
    await control.getByText('剪辑端尚未提供运行诊断', { exact: true }).waitFor()
    await control.getByText('handoff-isolated', { exact: true }).waitFor()
    diagnosticFailure = true
    await control.getByRole('status').filter({ hasText: '链路状态暂不可读取' }).waitFor({ timeout: 15000 })
    await control.getByText('渲染成片', { exact: true }).waitFor()
    await page.screenshot({ path: resolve(reportDir, 'diagnostics-' + width + '.png'), fullPage: true })
    diagnosticFailure = false
    await control.locator('summary').filter({ hasText: '链路状态' }).click()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    const play = control.getByRole('button', { name: '预览剪辑成片 1', exact: true })
    await play.click()
    await expect(page.getByRole('dialog').locator('video')).toBeVisible()
    await page.waitForFunction(() => document.querySelector('[role=dialog] video')?.readyState >= 2)
    await page.screenshot({ path: resolve(reportDir, 'preview-' + width + '.png'), fullPage: true })
    await page.getByRole('dialog').getByRole('button', { name: '关闭预览' }).focus()
    await page.keyboard.press('Escape')
    await page.getByRole('dialog').waitFor({ state: 'detached' })
    await expect(play).toBeFocused()
    const download = control.getByRole('button', { name: '下载剪辑成片 1', exact: true })
    mediaFailed = true
    await download.click()
    await control.getByRole('alert').filter({ hasText: '文件已经过期' }).waitFor()
    await refresh()
    await control.getByRole('alert').filter({ hasText: '文件已经过期' }).waitFor()
    mediaFailed = false
    const pendingDownload = page.waitForEvent('download')
    await download.click()
    const file = await pendingDownload
    assert.equal(file.suggestedFilename(), 'edited-render-1.mp4')
    assert.equal(await file.failure(), null)
    assert.deepEqual(await readFile(await file.path()), media)
    await page.getByRole('button', { name: '关闭任务详情', exact: true }).click()
    assert.equal(new URL(page.url()).searchParams.get('view'), 'records')
    assert.equal(new URL(page.url()).searchParams.get('filter'), 'done')
    assert.equal(new URL(page.url()).searchParams.get('q'), sourceTask.title)
    await page.locator('.records-completed .result-card').first().getByRole('button', { name: '剪辑与成片', exact: true }).click()
    await expect(timeline).toContainText('剪辑完成')
    await page.goBack()
    await expect(page.locator('.records-completed .result-card').first()).toBeVisible()
    await page.goForward()
    await expect(timeline).toContainText('剪辑完成')
    assert.equal(submissions, 1)
    // Lost submit response: reconcile the persisted handoff without another POST.
    handoffs = []; loseResponse = true
    await reloadEditing()
    await expect(send).toBeEnabled()
    await send.click()
    await expect(timeline).toContainText('等待传输')
    assert.equal(submissions, 2)
    await expect(control.getByRole('alert')).toHaveCount(0)
    // A missing edited frame keeps the card, preview and download reachable.
    handoffs[0].status = 'PENDING_REVIEW'; handoffs[0].outputs = [output]
    mediaFailed = true
    await reloadEditing()
    await expect(control.getByRole('img', { name: '剪辑成片 1封面，缩略图不可用', exact: true })).toBeVisible()
    await expect(thumbnail).toBeEnabled()
    await expect(download).toBeEnabled()
    mediaFailed = false
    role = 'AUDITOR'; handoffs = []
    await reloadEditing()
    await control.getByText('当前账号仅可查看剪辑结果', { exact: true }).waitFor()
    assert.equal(await send.count(), 0)
    assert.equal(bindings, 1)
    recordsAccess = false
    await page.goto(fixture.origin + '/__cutflow_fixture?view=cutflow')
    await expect(page.locator('.home-view')).toBeVisible()
    await expect(nav.getByRole('button', { name: '剪辑与成片', exact: true })).toHaveCount(0)
    await expect(page.locator('.cutflow-library')).toHaveCount(0)
    recordsAccess = true
    role = 'ADMIN'
    await page.goto(fixture.origin + '/__cutflow_fixture?view=make')
    const composer = page.locator('#image-to-video-panel')
    await composer.waitFor({ state: 'visible' })
    const advanced = composer.locator('.generation-advanced > summary')
    const hasRecommendedDefaults = await advanced.count() > 0
    if (hasRecommendedDefaults) await advanced.click()
    await expect(composer.getByLabel('站点风格')).toHaveValue('')
    await composer.getByRole('radio', { name: /自定义提示词/ }).click()
    const creationSite = composer.getByLabel('商品所属品牌')
    await expect(creationSite).toHaveValue('')
    await creationSite.selectOption('pagewings')
    await expect(creationSite).toHaveValue('pagewings')
    await page.getByRole('tab', { name: '视频复刻' }).click()
    await page.locator('#video-remix-panel').waitFor({ state: 'visible' })
    const deepAdvanced = page.locator('#video-remix-panel .generation-advanced > summary')
    if (await deepAdvanced.count()) await deepAdvanced.click()
    const deepSite = page.locator('#video-remix-panel').getByLabel('商品所属品牌')
    await expect(deepSite).toHaveValue('')
    await deepSite.selectOption('pagewings')
    await expect(deepSite).toHaveValue('pagewings')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.screenshot({ path: resolve(reportDir, 'creation-' + width + '.png'), fullPage: true })
    defaultProfileId = 'pagewings'
    await page.reload()
    await page.getByRole('tab', { name: '视频复刻' }).click()
    if (await deepAdvanced.count()) await deepAdvanced.click()
    await expect(deepSite).toHaveCount(0)
    report.cases.push({ width, passed: true, checks: ['single-editing-output-section', 'edited-thumbnail-decode-paused', 'thumbnail-matches-original-style', 'thumbnail-preview-and-focus', 'thumbnail-failure-fallback', 'task-center-shortcut', 'task-and-variant-url', 'reload', 'legacy-links', 'return-filter-query', 'history-navigation', 'timeline-pending-active-completed', 'timeline-auto-poll', 'permissions', 'double-click-once', 'site-binding', 'retry', 'source-filter', 'preview', 'download', 'lost-submit-response', 'no-overflow'] })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.externalRequests, [])

} finally {
  if (activePage && !activePage.isClosed()) {
    await activePage.screenshot({ path: resolve(reportDir, 'last-state.png'), fullPage: true }).catch(() => {})
    await writeFile(resolve(reportDir, 'last-state.txt'), await activePage.locator('body').innerText()).catch(() => {})
  }
  report.sources = {}
  for (const file of ['frontend/src/features/production/CutflowControl.tsx', 'frontend/src/features/production/CutflowSiteSelect.tsx', 'frontend/src/features/production/ProductionComposer.tsx', 'frontend/src/features/production/VideoRecreationPanel.tsx', 'frontend/src/features/production/cutflow.css', 'frontend/src/domain/cutflowPresentation.ts']) {
    report.sources[file] = createHash('sha256').update(await readFile(resolve(root, file))).digest('hex')
  }
  await writeFile(resolve(reportDir, 'browser-report.json'), JSON.stringify(report, null, 2))
  await browser.close()
  await fixture.server.close()
}
console.log(JSON.stringify(report))
