import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startClassicServer } from './classic_web_server.mjs'
import { createPageResponses } from './fixtures/ui-page-responses.mjs'

const root = resolve(fileURLToPath(new URL('..', import.meta.url)))
assert(process.env.PLAYWRIGHT_MODULE && process.env.ALLWE_BUILD_DIR && process.env.ALLWE_REPORT_DIR,
  'Set PLAYWRIGHT_MODULE, ALLWE_BUILD_DIR and external ALLWE_REPORT_DIR')
const reportDir = resolve(process.env.ALLWE_REPORT_DIR)
for (const tree of [root, resolve(root, '../libtv-classic-0e3d1c8-main')]) {
  const path = relative(tree, reportDir)
  assert(path.startsWith('..') || isAbsolute(path), 'Keep generated reports outside worktrees')
}
await mkdir(reportDir, { recursive: true })
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE)
const server = process.env.ALLWE_BASE_URL ? null : await startClassicServer({ root: resolve(process.env.ALLWE_BUILD_DIR), hosts: ['127.0.0.1'], port: 0, apiPort: 9 })
const origin = process.env.ALLWE_BASE_URL ? new URL(process.env.ALLWE_BASE_URL).origin : `http://127.0.0.1:${server.port}`
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const report = { checks: [], errors: [], unexpected: [] }
try {
  for (const width of [1440, 1024, 821, 820, 768, 390, 320]) {
    const context = await browser.newContext({ viewport: { width, height: width <= 480 ? 844 : 960 }, serviceWorkers: 'block' })
    const page = await context.newPage()
    const fixtures = createPageResponses({ empty: true })
    let authenticated = false
    let releaseLogin
    let outcome = 'invalid'
    let loginCount = 0
    let logoutCount = 0
    page.on('pageerror', error => report.errors.push(error.message))
    await page.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== origin) { report.unexpected.push(url.origin); return route.abort() }
      if (url.pathname === '/api/v1/auth/login') {
        loginCount++
        assert.equal(request.method(), 'POST')
        assert.deepEqual(request.postDataJSON(), { username: 'preview-user', password: 'synthetic-password' })
        assert.equal(request.headers()['x-user-id'], undefined)
        await new Promise(done => { releaseLogin = done })
        if (outcome === 'invalid') return route.fulfill({ status: 401, json: { detail: { code: 'INVALID_CREDENTIALS', message: 'Synthetic invalid account' } } })
        if (outcome === 'unavailable') return route.fulfill({ status: 503, json: { detail: { code: 'UNAVAILABLE', message: 'Synthetic unavailable' } } })
        authenticated = true
        return route.fulfill({ json: { csrfToken: 'synthetic-csrf' } })
      }
      if (url.pathname === '/api/v1/auth/logout') {
        assert.equal(request.method(), 'POST')
        assert.equal(request.headers()['x-csrf-token'], 'synthetic-csrf')
        logoutCount++
        authenticated = false
        return route.fulfill({ status: 204 })
      }
      if (request.method() !== 'GET') { report.unexpected.push(`${request.method()} ${url.pathname}`); return route.abort() }
      if (url.pathname === '/api/v1/auth/me') {
        return authenticated
          ? route.fulfill({ json: { ...fixtures.get(url.pathname), pagePermissions: ['records'] } })
          : route.fulfill({ status: 401, json: { detail: { code: 'UNAUTHENTICATED', message: 'Synthetic session required' } } })
      }
      if (fixtures.has(url.pathname)) return route.fulfill({ json: fixtures.get(url.pathname) })
      if (url.pathname === '/api/v1/account/generation-budget') return route.fulfill({ json: { day: '2026-10-22', currency: 'USD', dailyBudgetUsd: null, budgetUsedUsd: 0, unpricedCount: 0, prices: {} } })
      if (url.pathname.startsWith('/api/')) { report.unexpected.push(url.pathname); return route.abort() }
      return route.continue()
    })
    await page.goto(`${origin}/?view=records`)
    await page.locator('.allwe-login').waitFor()
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0) })
    await page.evaluate(() => document.fonts.ready)
    assert.equal(await page.title(), 'ALLWE 广告视频素材中心 · 经典版')
    const icon = await page.locator('link[rel="icon"]').getAttribute('href')
    assert.equal(icon, '/favicon.svg?v=allwe-20261022')
    const favicon = await page.request.get(`${origin}${icon}`)
    assert.equal(favicon.status(), 200)
    assert((await favicon.text()).includes('data:image/png;base64,'))
    const measure = async () => page.evaluate(() => {
      const box = selector => document.querySelector(selector).getBoundingClientRect()
      const footer = box('.allwe-login__footer')
      const form = box('.allwe-login__form')
      const images = [...document.querySelectorAll('.allwe-login img')]
      return {
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        footerClear: footer.top >= form.bottom,
        color: getComputedStyle(document.querySelector('.allwe-login__brand')).backgroundColor,
        logo: images.every(image => image.complete && image.naturalWidth === 1918
          && Math.abs(image.getBoundingClientRect().width / image.getBoundingClientRect().height - 1918 / 477) < .01),
      }
    })
    const layout = await measure()
    assert.equal(layout.overflow, false, `Login overflow: ${width}`)
    assert(layout.footerClear, `Login footer overlaps form: ${width}`)
    assert(layout.logo, `Logo missing or distorted: ${width}`)
    assert.equal(layout.color, 'rgb(25, 29, 31)')
    assert(await page.getByRole('button', { name: '登录工作台', exact: true }).isDisabled())
    if ([1440, 390, 320].includes(width)) await page.screenshot({ path: resolve(reportDir, `login-${width}.png`), fullPage: true })
    await page.getByLabel('账号', { exact: true }).fill('  preview-user  ')
    await page.getByLabel('密码', { exact: true }).fill('synthetic-password')
    await page.getByRole('button', { name: '显示密码', exact: true }).click()
    assert.equal(await page.locator('#login-password').getAttribute('type'), 'text')
    await page.getByRole('button', { name: '隐藏密码', exact: true }).click()
    assert.equal(await page.locator('#login-password').getAttribute('type'), 'password')
    for (const next of width === 1440 ? ['invalid', 'unavailable', 'success'] : ['invalid', 'success']) {
      outcome = next
      const requestReady = page.waitForRequest(request => new URL(request.url()).pathname === '/api/v1/auth/login')
      await page.getByRole('button', { name: '登录工作台', exact: true }).click()
      await requestReady
      await page.getByRole('button', { name: '正在验证账号', exact: true }).waitFor()
      assert.equal(await page.locator('.allwe-login__form').getAttribute('aria-busy'), 'true')
      assert(await page.locator('#login-username').isDisabled())
      assert(await page.getByRole('button', { name: '显示密码', exact: true }).isDisabled())
      assert.equal(typeof releaseLogin, 'function')
      releaseLogin()
      if (next !== 'success') {
        await page.getByRole('alert').filter({ hasText: next === 'invalid' ? '账号或密码不正确' : '网站暂时没有正常回应' }).waitFor()
        assert.equal(await page.locator('#login-username').getAttribute('aria-invalid'), 'true')
        assert.equal((await measure()).overflow, false)
      } else await page.locator('.app-shell').waitFor()
    }
    const logo = page.locator(width > 820 ? '.sidebar .product-logo' : '.mobile-topbar .product-logo')
    await logo.waitFor()
    const brand = await logo.evaluate(element => {
      const image = element.querySelector('img')
      const text = element.querySelector('strong')
      const a = image.getBoundingClientRect(), b = text.getBoundingClientRect()
      return { ready: image.complete && image.naturalWidth === 1918, left: a.left, right: Math.max(a.right, b.right), ratio: a.width / a.height, noOverlap: a.right <= b.left || a.bottom <= b.top }
    })
    assert(brand.ready && brand.noOverlap && brand.left >= 0 && brand.right <= width, `Application brand layout: ${width}`)
    assert(Math.abs(brand.ratio - 1918 / 477) < .01)
    if (width > 820) {
      const alignment = await page.evaluate(() => ({
        logo: document.querySelector('.sidebar .product-logo img').getBoundingClientRect().left,
        name: document.querySelector('.sidebar .product-logo strong').getBoundingClientRect().left,
        navigation: document.querySelector('.sidebar-main-nav button svg').getBoundingClientRect().left,
      }))
      assert(Math.abs(alignment.logo - alignment.navigation) < 1, `Sidebar logo alignment: ${width}`)
      assert(Math.abs(alignment.name - alignment.navigation) < 1, `Sidebar name alignment: ${width}`)
      report.checks.push({ width, alignment })
      if (width === 1440) await page.locator('.sidebar').screenshot({ path: resolve(reportDir, 'sidebar-aligned.png') })
    }
    if ([1440, 390].includes(width)) await logo.screenshot({ path: resolve(reportDir, `brand-${width}.png`) })
    if (width === 1440) {
      await page.getByRole('button', { name: '账号', exact: true }).click()
      await page.getByRole('button', { name: '退出登录', exact: true }).click()
      await page.locator('.allwe-login').waitFor()
      assert.equal(logoutCount, 1)
    }
    report.checks.push({ width, layout, brand, loginCount, logoutCount, authentication: 'mocked' })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.unexpected, [])
  await writeFile(resolve(reportDir, 'report.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ widthsPassed: report.checks.length, errors: report.errors, unexpectedRequests: report.unexpected }))
} finally {
  await browser.close()
  await server?.close()
}
