import http from 'node:http'
import { createReadStream } from 'node:fs'
import { realpath, stat } from 'node:fs/promises'
import { extname, isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { activeClassicApiPort, validClassicPorts } from './classic_api_routing.mjs'

const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.json': 'application/json' }
const csp = "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'"

export async function startClassicServer({ root, hosts, port, apiPort, alternateApiPort, apiRouteFile }) {
  const allowedApiPorts = validClassicPorts(apiPort, alternateApiPort)
  if (apiRouteFile && !alternateApiPort) throw new Error('Hot API routing requires an alternate API port')
  const route = () => activeClassicApiPort({ routeFile: apiRouteFile, primaryPort: apiPort, alternatePort: alternateApiPort })
  const dist = await realpath(root)
  await stat(resolve(dist, 'index.html'))
  const servers = []
  const inFlightByPort = new Map(allowedApiPorts.map(value => [value, 0]))
  let pendingRouteSelections = 0
  const handler = async (request, response) => {
    response.setHeader('X-Content-Type-Options', 'nosniff')
    response.setHeader('Referrer-Policy', 'same-origin')
    response.setHeader('Content-Security-Policy', csp)
    const requestHost = (request.headers.host || '').toLowerCase()
    const allowed = hosts.some(host => requestHost === `${host}:${port}`)
      || requestHost === `localhost:${port}`
    if (!allowed) { response.writeHead(403); response.end('Host not allowed'); return }
    if (request.url === '/__classic_api_route/status') {
      if (request.method !== 'GET' || !['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(request.socket.remoteAddress)) {
        response.writeHead(403); response.end(); return
      }
      try {
        const activeApiPort = await route()
        response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
        response.end(JSON.stringify({ hotRouteVersion: apiRouteFile ? 1 : 0,
          activeApiPort, allowedApiPorts, configured: Boolean(apiRouteFile),
          inFlightByPort: Object.fromEntries(inFlightByPort),
          drainContractVersion: 1, pendingRouteSelections }))
      } catch {
        response.writeHead(503, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
        response.end('{"code":"CLASSIC_API_ROUTE_INVALID"}')
      }
      return
    }
    if (request.url === '/api' || request.url.startsWith('/api/')) {
      let targetPort
      pendingRouteSelections += 1
      try { targetPort = await route() } catch {
        pendingRouteSelections -= 1
        response.writeHead(503, { 'Content-Type': 'application/json' })
        response.end('{"detail":"Classic API routing configuration unavailable"}')
        return
      }
      if (request.destroyed) { pendingRouteSelections -= 1; return }
      // Once a request chooses its upstream, a later route switch never moves
      // this in-flight request to a different backend.
      inFlightByPort.set(targetPort, inFlightByPort.get(targetPort) + 1)
      pendingRouteSelections -= 1
      let accounted = false
      const finish = () => {
        if (accounted) return
        accounted = true
        inFlightByPort.set(targetPort, inFlightByPort.get(targetPort) - 1)
      }
      response.once('finish', finish)
      response.once('close', finish)
      const upstream = http.request({ hostname: '127.0.0.1', port: targetPort, method: request.method,
        path: request.url, headers: { ...request.headers, 'x-forwarded-host': request.headers.host,
          'x-forwarded-proto': 'http' } }, incoming => {
        response.writeHead(incoming.statusCode, incoming.headers)
        incoming.pipe(response)
      })
      upstream.setTimeout(300_000, () => upstream.destroy(new Error('Upstream timeout')))
      upstream.on('error', () => {
        if (!response.headersSent) response.writeHead(502, { 'Content-Type': 'application/json' })
        response.end('{"detail":"Classic local API unavailable"}')
      })
      request.on('aborted', () => upstream.destroy())
      response.on('close', () => { if (!response.writableEnded) upstream.destroy() })
      request.pipe(upstream)
      return
    }
    if (!['GET', 'HEAD'].includes(request.method)) {
      response.writeHead(405, { Allow: 'GET, HEAD' }); response.end(); return
    }
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://local').pathname)
      if (pathname.includes('\0') || pathname.includes('\\')) throw new Error('Invalid path')
      let file = resolve(dist, `.${pathname === '/' ? '/index.html' : pathname}`)
      const outside = candidate => {
        const path = relative(dist, candidate)
        return path.startsWith('..') || isAbsolute(path)
      }
      if (outside(file)) throw new Error('Outside static root')
      try { file = await realpath(file) }
      catch {
        if (extname(pathname)) { response.writeHead(404); response.end(); return }
        file = resolve(dist, 'index.html')
      }
      if (outside(file)) throw new Error('Outside static root')
      const info = await stat(file)
      if (!info.isFile()) { response.writeHead(404); response.end(); return }
      response.writeHead(200, { 'Content-Type': types[extname(file)] || 'application/octet-stream',
        'Content-Length': info.size,
        'Cache-Control': extname(file) === '.html' ? 'no-store' : 'public, max-age=31536000, immutable' })
      if (request.method === 'HEAD') response.end()
      else createReadStream(file).on('error', () => response.destroy()).pipe(response)
    } catch { response.writeHead(400); response.end('Invalid static request') }
  }
  try {
    for (const host of hosts) {
      const server = http.createServer(handler)
      await new Promise((accept, reject) => {
        server.once('error', reject)
        server.listen(port, host, accept)
      })
      if (port === 0) port = server.address().port
      servers.push(server)
    }
  } catch (error) {
    await Promise.all(servers.map(server => new Promise(done => server.close(done))))
    throw error
  }
  return { port, servers, close: () => Promise.all(servers.map(server => new Promise(done => {
    server.closeAllConnections(); server.close(done)
  }))) }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({ options: { root: { type: 'string' }, host: { type: 'string' },
    port: { type: 'string' }, 'api-port': { type: 'string' },
    'alternate-api-port': { type: 'string' }, 'api-route-file': { type: 'string' } } })
  if (!values.root || !values.host || !values.port || !values['api-port']) throw new Error('Missing server arguments')
  const service = await startClassicServer({ root: values.root,
    hosts: [...new Set([values.host, '127.0.0.1'])], port: Number(values.port), apiPort: Number(values['api-port']),
    alternateApiPort: values['alternate-api-port'] ? Number(values['alternate-api-port']) : undefined,
    apiRouteFile: values['api-route-file'] })
  console.log(`Classic build ready: http://${values.host}:${service.port}/`)
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => {
    await service.close(); process.exit(0)
  })
}
