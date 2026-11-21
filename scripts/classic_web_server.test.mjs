import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { startClassicServer } from './classic_web_server.mjs'

test('static build and same-origin proxy preserve login without exposing source or files', async () => {
  const temp = await mkdtemp(join(tmpdir(), 'classic-web-'))
  const dist = join(temp, 'dist')
  await mkdir(join(dist, 'assets'), { recursive: true })
  await writeFile(join(dist, 'index.html'), '<html>Classic build</html>')
  await writeFile(join(dist, 'assets', 'app.js'), '/* compiled app */')
  await writeFile(join(temp, 'session-secret.txt'), 'private-local-fixture')
  const upstream = http.createServer((request, response) => {
    const body = []
    request.on('data', chunk => body.push(chunk))
    request.on('end', () => {
      response.writeHead(200, { 'Content-Type': 'application/json',
        'Set-Cookie': ['session=fixture; HttpOnly; SameSite=Strict; Path=/', 'csrf=fixture; Path=/'] })
      response.end(JSON.stringify({ method: request.method, path: request.url,
        body: Buffer.concat(body).toString(), csrf: request.headers['x-csrf-token'] }))
    })
  })
  await new Promise(done => upstream.listen(0, '127.0.0.1', done))
  let service
  try {
    service = await startClassicServer({ root: dist, hosts: ['127.0.0.1'],
      port: 0, apiPort: upstream.address().port })
    const origin = `http://127.0.0.1:${service.port}`
    const home = await fetch(`${origin}/`)
    assert.equal(home.status, 200)
    assert.match(await home.text(), /Classic build/)
    assert.equal(home.headers.get('cache-control'), 'no-store')
    assert.equal((await fetch(`${origin}/assets/app.js`)).status, 200)
    assert.equal((await fetch(`${origin}/src/main.tsx`)).status, 404)
    assert.equal((await fetch(`${origin}/session-secret.txt`)).status, 404)
    assert.equal((await fetch(`${origin}/..%5csession-secret.txt`)).status, 400)
    assert.equal((await fetch(`${origin}/`, { method: 'POST' })).status, 405)
    const result = await fetch(`${origin}/api/v1/auth/login`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'fixture-token' },
      body: JSON.stringify({ username: 'fixture' }) })
    assert.equal(result.headers.getSetCookie().length, 2)
    assert.deepEqual(await result.json(), { method: 'POST', path: '/api/v1/auth/login',
      body: '{"username":"fixture"}', csrf: 'fixture-token' })
    const badHost = await new Promise((accept, reject) => {
      http.get(`${origin}/`, { headers: { Host: 'unrelated.example' } }, response => {
        response.resume(); accept(response.statusCode)
      }).on('error', reject)
    })
    assert.equal(badHost, 403)
  } finally {
    if (service) await service.close()
    upstream.closeAllConnections()
    await new Promise(done => upstream.close(done))
    await rm(temp, { recursive: true, force: true })
  }
})
