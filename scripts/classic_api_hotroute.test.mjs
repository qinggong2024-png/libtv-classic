import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { startClassicServer } from './classic_web_server.mjs'
import { activeClassicApiPort, atomicallySetClassicApiPort } from './classic_api_routing.mjs'
import { inspectClassicApiRoute, switchClassicApiRoute } from './classic_api_hot_switch.mjs'

async function listen(server) {
  await new Promise((resolve, reject) => server.once('error', reject).listen(0, '127.0.0.1', resolve))
  return server.address().port
}
async function close(server) {
  server.closeAllConnections()
  await new Promise(done => server.close(done))
}
async function waitFor(check, limit = 40) {
  for (let i = 0; i < limit; i++) {
    if (await check()) return
    await new Promise(resolve => setTimeout(resolve, 10))
  }
  throw new Error('timed out waiting for synthetic request')
}

test('hot route keeps the old in-flight POST on old API and sends NEW requests to green', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'classic-hot-route-'))
  let old, green, web
  try {
    const dist = join(dir, 'dist')
    await mkdir(join(dist, 'assets'), { recursive: true })
    await writeFile(join(dist, 'index.html'), '<html>unchanged</html>')
    await writeFile(join(dist, 'assets', 'app.js'), '/* immutable */')
    let releaseOld
    let oldReceived
    const oldArrival = new Promise(resolve => { oldReceived = resolve })
    const oldRelease = new Promise(resolve => { releaseOld = resolve })
    old = http.createServer(async (req, res) => {
      oldReceived()
      await oldRelease
      res.writeHead(200, { 'Content-Type': 'application/json', 'Set-Cookie': 'test=old; HttpOnly' })
      res.end(JSON.stringify({ selected: 'old', method: req.method, cookie: req.headers.cookie }))
    })
    green = http.createServer((req, res) => {
      res.writeHead(200, { 'Content-Type': 'application/json', 'Set-Cookie': 'test=green; HttpOnly' })
      res.end(JSON.stringify({ selected: 'green', method: req.method, cookie: req.headers.cookie }))
    })
    const primaryPort = await listen(old)
    const alternatePort = await listen(green)
    const routeFile = join(dir, 'route.json')
    web = await startClassicServer({
      root: dist, hosts: ['127.0.0.1'], port: 0, apiPort: primaryPort, alternateApiPort: alternatePort, apiRouteFile: routeFile,
    })
    const base = `http://127.0.0.1:${web.port}`
    assert.equal((await (await fetch(base)).text()), '<html>unchanged</html>')
    const startedOld = fetch(`${base}/api/v1/tasks`, { method: 'POST', headers: { cookie: 'a=b' }, body: '{}' })
    await oldArrival
    await waitFor(async () => (await (await fetch(`${base}/__classic_api_route/status`)).json())
      .inFlightByPort[String(primaryPort)] === 1)
    await atomicallySetClassicApiPort({ routeFile, targetPort: alternatePort })
    const status = await (await fetch(`${base}/__classic_api_route/status`)).json()
    assert.equal(status.activeApiPort, alternatePort)
    assert.equal(status.inFlightByPort[String(primaryPort)], 1)
    const newReply = await fetch(`${base}/api/v1/tasks`, { method: 'POST', headers: { cookie: 'a=b' }, body: '{}' })
    assert.equal((await newReply.json()).selected, 'green')
    assert.equal(newReply.headers.get('set-cookie'), 'test=green; HttpOnly')
    releaseOld()
    const oldReply = await startedOld
    assert.equal((await oldReply.json()).selected, 'old')
    assert.equal(oldReply.headers.get('set-cookie'), 'test=old; HttpOnly')
    await waitFor(async () => (await (await fetch(`${base}/__classic_api_route/status`)).json())
      .inFlightByPort[String(primaryPort)] === 0)
    assert.equal((await fetch(`${base}/assets/app.js`)).status, 200)
    // A green API crash must never silently retry a potentially paid POST
    // against the still-healthy old API. An operator must explicitly roll back.
    await close(green)
    green = null
    const crashedReply = await fetch(`${base}/api/v1/tasks`, { method: 'POST', body: '{}' })
    assert.equal(crashedReply.status, 502)
    await atomicallySetClassicApiPort({ routeFile, targetPort: primaryPort })
    const afterRollback = await fetch(`${base}/api/v1/tasks`, { method: 'POST', body: '{}' })
    assert.equal((await afterRollback.json()).selected, 'old')
  } finally {
    if (web) await web.close()
    if (old) await close(old)
    if (green) await close(green)
    await rm(dir, { recursive: true, force: true })
  }
})

test('invalid or malicious routing configuration fails closed, never chooses an arbitrary localhost port', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'classic-invalid-route-'))
  let api, web
  try {
    const dist = join(dir, 'dist')
    await mkdir(join(dist, 'assets'), { recursive: true })
    await writeFile(join(dist, 'index.html'), '<html>safe</html>')
    api = http.createServer((_req, res) => res.end('old'))
    const port = await listen(api)
    const routeFile = join(dir, 'route.json')
    web = await startClassicServer({
      root: dist, hosts: ['127.0.0.1'], port: 0, apiPort: port, alternateApiPort: port + 1, apiRouteFile: routeFile,
    })
    const origin = `http://127.0.0.1:${web.port}`
    assert.equal((await (await fetch(`${origin}/api/v1/ping`)).text()), 'old')
    await writeFile(routeFile, JSON.stringify({ version: 1, activeApiPort: 22222 }))
    assert.equal((await fetch(`${origin}/api/v1/ping`)).status, 503)
    assert.equal((await fetch(`${origin}/__classic_api_route/status`)).status, 503)
    await writeFile(routeFile, '{broken')
    assert.equal((await fetch(`${origin}/api/v1/ping`)).status, 503)
    await rm(routeFile)
    assert.equal((await (await fetch(`${origin}/api/v1/ping`)).text()), 'old')
  } finally {
    if (web) await web.close()
    if (api) await close(api)
    await rm(dir, { recursive: true, force: true })
  }
})


test('operator apply requires an explicit production runtime route file, never guessed from release worktree', () => {
  const cli = fileURLToPath(new URL('./classic_api_hot_switch.mjs', import.meta.url))
  const result = spawnSync(process.execPath, [
    cli, 'switch', '--expected-current', '8002', '--target', '8003',
    '--expected-source-revision', 'a'.repeat(64), '--apply',
  ], { encoding: 'utf8', timeout: 3000 })
  assert.equal(result.status, 2)
  assert.match(result.stderr, /requires explicit --route-file/)
})

test('operator requires matching source revision, explicit apply, compare-and-swap route and live adoption', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'classic-route-operator-'))
  const routeFile = join(dir, 'routing.json')
  const revision = 'a'.repeat(64)
  const ports = [28002, 28003]
  const source = { deploymentId: 'libtv-classic-lan-0e3d1c8', sourceRevision: revision }
  let versionSupported = true
  let intakePaused = true
  const fetchJson = async url => {
    if (url.endsWith('/__classic_api_route/status')) {
      return {
        hotRouteVersion: versionSupported ? 1 : 0,
        configured: versionSupported,
        allowedApiPorts: ports,
        activeApiPort: await activeClassicApiPort({ routeFile, primaryPort: ports[0], alternatePort: ports[1] }),
        inFlightByPort: { '28002': 0, '28003': 0 },
      }
    }
    if (url.endsWith('/api/v1/health/ready')) return { status: 'ready' }
    if (url.endsWith('/api/v1/system/classic-new-video-admission')) return {
      gateVersion: 1, configured: true, deploymentId: 'libtv-classic-lan-0e3d1c8',
      newVideoAdmissionsPaused: intakePaused,
    }
    if (url.endsWith('/api/v1/system/status')) return {
      ...source, configRevision: 'stable-config', schemaRevision: 'stable-schema',
    }
    throw new Error('unexpected URL')
  }
  try {
    assert.equal((await switchClassicApiRoute({
      routeFile, expectedCurrent: ports[0], targetPort: ports[1], expectedSourceRevision: revision, fetchJson,
    })).action, 'dry-run')
    assert.equal(await activeClassicApiPort({ routeFile, primaryPort: ports[0], alternatePort: ports[1] }), ports[0])
    await assert.rejects(switchClassicApiRoute({
      routeFile, expectedCurrent: ports[0], targetPort: ports[1],
      expectedSourceRevision: 'b'.repeat(64), fetchJson, apply: true,
    }), /revision/)
    await assert.rejects(switchClassicApiRoute({
      routeFile, expectedCurrent: ports[0], targetPort: 26000,
      expectedSourceRevision: revision, fetchJson, apply: true,
    }), /approved/)
    versionSupported = false
    await assert.rejects(inspectClassicApiRoute({ fetchJson }), /does not support/)
    versionSupported = true
    intakePaused = false
    await assert.rejects(switchClassicApiRoute({
      routeFile, expectedCurrent: ports[0], targetPort: ports[1],
      expectedSourceRevision: revision, fetchJson, apply: true,
    }), /paused intake gate/)
    intakePaused = true
    assert.equal((await switchClassicApiRoute({
      routeFile, expectedCurrent: ports[0], targetPort: ports[1],
      expectedSourceRevision: revision, fetchJson, apply: true,
    })).action, 'switched')
    assert.equal(JSON.parse(await readFile(routeFile, 'utf8')).activeApiPort, ports[1])
    await assert.rejects(switchClassicApiRoute({
      routeFile, expectedCurrent: ports[0], targetPort: ports[1],
      expectedSourceRevision: revision, fetchJson, apply: true,
    }), /routing state changed/)
    assert.equal((await switchClassicApiRoute({
      routeFile, expectedCurrent: ports[1], targetPort: ports[0],
      expectedSourceRevision: revision, fetchJson, apply: true,
    })).action, 'switched')
    assert.equal(await activeClassicApiPort({
      routeFile, primaryPort: ports[0], alternatePort: ports[1],
    }), ports[0])
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
})
