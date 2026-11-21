// Exercise the built classic UI using synthetic responses; every live API call is blocked.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'
import { createAdminResponses } from './fixtures/ui-admin-responses.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const frontendRequire = createRequire(resolve(root, 'frontend/package.json'))
const { preview } = await import(pathToFileURL(frontendRequire.resolve('vite')).href)
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const reportDir = resolve(process.env.UI_COPY_REPORT_DIR)
await mkdir(reportDir, { recursive: true })
const server = await preview({ configFile: false, envFile: false, root: resolve(root, 'frontend'), preview: { host: '127.0.0.1', port: 0 } })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const origin = server.resolvedUrls.local[0].replace(/\/$/, '')
const results = []
const writes = []
const forbidden = /SYNTHETIC_FAILURE|SYNTHETIC_LONG_ERROR|KLING_API_RATE_LIMITED|WAVESPEED_SUBMISSION_UNCERTAIN|INTERNAL_ERROR|Task ID|Session ID|Prompt Hash|技术信息|幂等键|工作节点心跳|GET 和 PATCH/

try {
  for (const width of [1440, 390]) {
    for (const scenario of ['records', 'providers', 'operations', 'images', 'server-error']) {
      const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: 'block', reducedMotion: 'reduce' })
      const page = await context.newPage()
      page.setDefaultTimeout(12000)
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      const responses = new Map([...createPageResponses(), ...createAdminResponses()])
      responses.get('/api/v1/auth/me').username = 'classic-admin'
      responses.get('/api/v1/admin/operations').runners = []
      const tasks = responses.get('/api/v1/tasks')
      tasks[0].title = '需要检查的服装视频'
      tasks[0].variants[0].errorCode = 'KLING_API_RATE_LIMITED'
      tasks[0].variants[0].providerErrorMessage = 'Too many requests'
      tasks[0].variants[0].retryable = true
      responses.set('/api/v1/image-generation-batches', { items: [{
        id: 'fixture-batch', provider: 'gpt', createdAt: new Date().toISOString(), configuration: { modes: ['fusion'] },
        items: [{ id: 'fixture-image', clientTaskId: 'fixture-image', outfitId: 'fixture-outfit', outfitName: '待核对的图片',
          status: 'write_uncertain', providerTaskIds: [], outputs: [],
          error: { code: 'WAVESPEED_SUBMISSION_UNCERTAIN', message: 'Socket timeout', retryable: false },
        }],
      }], hasMore: false, nextOffset: 20 })
      await context.route('**/*', async route => {
        const request = route.request()
        const url = new URL(request.url())
        if (url.origin !== origin) return route.abort()
        if (!url.pathname.startsWith('/api/')) return route.continue()
        if (request.method() !== 'GET') {
          writes.push(request.method() + ' ' + url.pathname)
          return route.fulfill({ status: 405, json: {} })
        }
        if (scenario === 'server-error' && url.pathname === '/api/v1/admin/operations') {
          return route.fulfill({ status: 503, json: { detail: { code: 'INTERNAL_ERROR', message: 'Traceback: database unavailable' } } })
        }
        if (url.pathname.startsWith('/api/v1/fixture-media/')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="320"><rect width="240" height="320" fill="#ddd"/></svg>' })
        if (url.pathname === '/api/v1/video-tasks/deep/status') return route.fulfill({ json: { enabled: false, reason: '后台执行器未在线' } })
        if (url.pathname.includes('/elements')) return route.fulfill({ json: { items: [], total: 0 } })
        if (url.pathname.startsWith('/api/v1/image-generation-batches/')) return route.fulfill({ json: responses.get('/api/v1/image-generation-batches').items[0] })
        const body = responses.get(url.pathname) ?? (url.pathname.startsWith('/api/v1/tasks/') ? tasks[0] : [])
        return route.fulfill({ json: body })
      })
      const query = scenario === 'records' ? '?view=records' : scenario === 'images' ? '?view=images' : `?view=admin${scenario === 'providers' ? '&section=providers' : ''}`
      try {
        await page.goto(origin + '/' + query)
        await page.waitForFunction(() => !document.querySelector('.app-boot-screen,.page-loading'))
        if (scenario === 'records') {
          await page.locator('.task-table-row').filter({ hasText: '需要检查的服装视频' }).click()
          const details = page.locator('.technical-details')
          await details.locator('summary').click()
          await page.getByText('生成服务现在比较繁忙，请稍等一会儿再试。', { exact: false }).first().waitFor()
          assert((await details.innerText()).includes('任务编号'))
        } else if (scenario === 'images') {
          await page.waitForFunction(() => document.querySelector('.image-generation-submit__hint')?.textContent === '')
          await page.getByRole('button', { name: '生图记录', exact: false }).click()
          const details = page.locator('.image-task-technical').first()
          await details.locator('summary').click()
          await page.getByText('确认前不要重新生成', { exact: false }).first().waitFor()
          const recovery = page.getByRole('button', { name: '核对状态', exact: true })
          assert(await recovery.evaluate(button => button.scrollHeight <= button.clientHeight + 1 && button.scrollWidth <= button.clientWidth + 1), 'Recovery action is clipped')
          assert((await details.innerText()).includes('生成服务查询编号'))
        } else if (scenario === 'providers') {
          await page.getByRole('button', { name: '检查连接', exact: true }).first().waitFor()
          assert((await page.locator('.provider-section').first().innerText()).includes('服务密钥'))
        } else if (scenario === 'operations') {
          await page.getByText('暂时没有生成电脑在线，请联系管理员启动后台生成程序。', { exact: true }).waitFor()
        } else {
          await page.getByText('网站暂时无法完成这项操作，请稍后再试；仍有问题时请联系管理员。', { exact: true }).waitFor()
        }
        const text = await page.locator('body').innerText()
        assert(!forbidden.test(text), 'Raw technical content is visible')
        assert.deepEqual(errors, [], 'Browser runtime errors')
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Page overflows horizontally')
        await page.screenshot({ path: resolve(reportDir, `${scenario}-${width}.png`), fullPage: true })
        results.push({ scenario, width, passed: true })
      } catch (error) {
        await page.screenshot({ path: resolve(reportDir, `${scenario}-${width}-failed.png`), fullPage: true })
        results.push({ scenario, width, passed: false, error: error.message })
      } finally { console.log(JSON.stringify(results.at(-1))); await context.close() }
    }
  }
} finally {
  await browser.close()
  await new Promise(resolveClose => server.httpServer.close(resolveClose))
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify({ results, writes }, null, 2))
}
console.log(JSON.stringify({ results, writes }, null, 2))
assert.equal(writes.length, 0)
assert(results.every(result => result.passed), 'Plain-language browser checks failed')
