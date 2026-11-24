/**
 * Guarded operator switch for a *running* Classic hot-route-enabled web server.
 * Safe by default: status/readiness only. Does not start/stop API or workers.
 * The active backend changes only with an explicit --apply.
 *
 * Usage after the hot-route-aware web server has been installed:
 *   node scripts/classic_api_hot_switch.mjs status
 *   node scripts/classic_api_hot_switch.mjs switch --expected-current 8002 --target 8003 \
 *     --expected-source-revision <64-character-source-fingerprint> \
 *     --route-file E:\\Projects\\libtv-classic-0e3d1c8-main\\var\\classic-lan\\runtime\\api-routing.json --apply
 */
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { open, rm } from 'node:fs/promises'
import { parseArgs } from 'node:util'
import { activeClassicApiPort, atomicallySetClassicApiPort } from './classic_api_routing.mjs'

const PROJECT = dirname(dirname(fileURLToPath(import.meta.url)))
const DEFAULT_ROUTE_FILE = join(PROJECT, 'var', 'classic-lan', 'runtime', 'api-routing.json')
const EXPECTED_DEPLOYMENT = 'libtv-classic-lan-0e3d1c8'

async function readJson(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(5000), cache: 'no-store' })
  if (!response.ok) throw new Error(`HTTP ${response.status} at ${new URL(url).pathname}`)
  return await response.json()
}

export async function inspectClassicApiRoute({ webPort = 5176, fetchJson = readJson } = {}) {
  const status = await fetchJson(`http://127.0.0.1:${webPort}/__classic_api_route/status`)
  if (status.hotRouteVersion !== 1 || status.configured !== true ||
      !Array.isArray(status.allowedApiPorts) || status.allowedApiPorts.length !== 2 ||
      !status.allowedApiPorts.includes(status.activeApiPort)) {
    throw new Error('Active 5176 web server does not support the classic hot-route contract')
  }
  return status
}

export async function switchClassicApiRoute({
  routeFile = DEFAULT_ROUTE_FILE,
  webPort = 5176,
  expectedCurrent,
  targetPort,
  expectedSourceRevision,
  apply = false,
  fetchJson = readJson,
} = {}) {
  if (!Number.isInteger(expectedCurrent) || !Number.isInteger(targetPort) ||
      expectedCurrent === targetPort) {
    throw new Error('Provide distinct integer --expected-current and --target API ports')
  }
  if (!/^[0-9a-f]{64}$/.test(expectedSourceRevision || '')) {
    throw new Error('Specify the full 64-character target source fingerprint')
  }
  const lockPath = join(dirname(routeFile), 'api-routing.lock')
  let lock
  try {
    // This is an operator lock, not an end-user task lease.
    // Concurrent operators may never validate an old route and both switch it.
    if (apply) lock = await open(lockPath, 'wx')
    const before = await inspectClassicApiRoute({ webPort, fetchJson })
    if (before.activeApiPort !== expectedCurrent || !before.allowedApiPorts.includes(targetPort)) {
      throw new Error('API routing state changed or target is not an approved loopback port')
    }
    const direct = `http://127.0.0.1:${targetPort}`
    const original = `http://127.0.0.1:${expectedCurrent}`
    const ready = await fetchJson(`${direct}/api/v1/health/ready`)
    if (!ready || typeof ready !== 'object') throw new Error('Target health response unavailable')
    // The intake gate is the read-only identity handshake, not system/status.
    // Both versions must already be installed, frozen and wired to this classic deployment.
    const oldGate = await fetchJson(`${original}/api/v1/system/classic-new-video-admission`)
    const newGate = await fetchJson(`${direct}/api/v1/system/classic-new-video-admission`)
    for (const gate of [oldGate, newGate]) {
      if (gate.gateVersion !== 1 || gate.deploymentId !== EXPECTED_DEPLOYMENT ||
          gate.configured !== true || gate.newVideoAdmissionsPaused !== true) {
        throw new Error('Both classic API deployments must verify the SAME paused intake gate')
      }
    }
    const oldConfig = await fetchJson(`${original}/api/v1/system/status`)
    const source = await fetchJson(`${direct}/api/v1/system/status`)
    if (source.sourceRevision !== expectedSourceRevision ||
        !source.configRevision || source.configRevision !== oldConfig.configRevision ||
        !source.schemaRevision || source.schemaRevision !== oldConfig.schemaRevision) {
      throw new Error('Target API revision, configuration or schema is incompatible with the active API')
    }
    if (!apply) return { action: 'dry-run', from: expectedCurrent, to: targetPort,
      expectedSourceRevision, inFlightByPort: before.inFlightByPort }
    // Recheck the on-disk routing identity under the lock before atomically replacing it.
    const onDisk = await activeClassicApiPort({
      routeFile, primaryPort: before.allowedApiPorts[0], alternatePort: before.allowedApiPorts[1],
    })
    if (onDisk !== expectedCurrent) throw new Error('On-disk route was changed by another operator')
    await atomicallySetClassicApiPort({ routeFile, targetPort })
    let after
    try { after = await inspectClassicApiRoute({ webPort, fetchJson }) } catch {}
    if (after?.activeApiPort !== targetPort) {
      // Do not claim a successful switch when the actual running 5176 server
      // has not observed it. Restore the original route under the same lock.
      await atomicallySetClassicApiPort({ routeFile, targetPort: expectedCurrent })
      throw new Error('Running web server did not adopt new API route; original route restored')
    }
    return { action: 'switched', from: expectedCurrent, to: targetPort,
      inFlightByPort: after.inFlightByPort }
  } finally {
    if (lock) {
      await lock.close()
      await rm(lockPath, { force: true })
    }
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const [action, ...args] = process.argv.slice(2)
  const { values } = parseArgs({ args, options: {
    'expected-current': { type: 'string' }, target: { type: 'string' },
    'expected-source-revision': { type: 'string' }, 'web-port': { type: 'string' },
    apply: { type: 'boolean', default: false },
    'route-file': { type: 'string' },
  } })
  try {
    if (action === 'status') {
      console.log(JSON.stringify(await inspectClassicApiRoute({
        webPort: Number(values['web-port'] || '5176'),
      }), null, 2))
    } else if (action === 'switch') {
      if (values.apply && !values['route-file']) {
        throw new Error('--apply requires explicit --route-file for the installed classic runtime')
      }
      console.log(JSON.stringify(await switchClassicApiRoute({
        expectedCurrent: Number(values['expected-current']),
        targetPort: Number(values.target),
        expectedSourceRevision: values['expected-source-revision'],
        routeFile: values['route-file'] || DEFAULT_ROUTE_FILE,
        webPort: Number(values['web-port'] || '5176'),
        apply: values.apply,
      }), null, 2))
    } else throw new Error('Usage: status | switch [--expected-current N --target N --expected-source-revision FULL_SHA --apply]')
  } catch (error) {
    console.error(`Classic API route switch refused: ${error.message}`)
    process.exitCode = 2
  }
}
