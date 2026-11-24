/**
 * Classic-only loopback API switch. Never accepts arbitrary upstream hosts or ports.
 * The web server reads the entire small file per NEW request; existing proxied
 * requests retain the port selected at their start.
 *
 * With an alternate API configured, a missing route file must fail closed.
 * Missing / malformed / unreadable hot-route file = 503, NOT a fallback.
 */
import { readFile, mkdir, rename, rm, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'

export class InvalidClassicRoute extends Error {}

export function validClassicPorts(primaryPort, alternatePort) {
  if (!Number.isInteger(primaryPort) || primaryPort < 1 || primaryPort > 65535) {
    throw new InvalidClassicRoute('Invalid primary API port')
  }
  if (alternatePort !== undefined && alternatePort !== null &&
      (!Number.isInteger(alternatePort) || alternatePort < 1024 ||
       alternatePort > 65535 || alternatePort === primaryPort)) {
    throw new InvalidClassicRoute('Invalid alternate API port')
  }
  return [primaryPort, ...(alternatePort == null ? [] : [alternatePort])]
}

export async function activeClassicApiPort({ routeFile, primaryPort, alternatePort }) {
  const allowed = validClassicPorts(primaryPort, alternatePort)
  if (!routeFile) return primaryPort
  let raw
  try {
    raw = await readFile(routeFile, 'utf8')
  } catch (error) {
    if (error?.code === 'ENOENT' && alternatePort == null) return primaryPort
    throw new InvalidClassicRoute('API route file unavailable')
  }
  if (raw.length > 512) throw new InvalidClassicRoute('API route file too large')
  let value
  try { value = JSON.parse(raw) } catch { throw new InvalidClassicRoute('Invalid API route JSON') }
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== 'activeApiPort,version' ||
      value.version !== 1 || !allowed.includes(value.activeApiPort)) {
    throw new InvalidClassicRoute('Invalid or unsupported API route')
  }
  return value.activeApiPort
}

export async function atomicallySetClassicApiPort({ routeFile, targetPort }) {
  if (!routeFile || !Number.isInteger(targetPort)) throw new InvalidClassicRoute('Invalid route target')
  const target = resolve(routeFile)
  const temporary = resolve(dirname(target), `.api-route-${randomUUID()}.tmp`)
  await mkdir(dirname(target), { recursive: true })
  try {
    await writeFile(temporary, JSON.stringify({ version: 1, activeApiPort: targetPort }) + '\n', {
      encoding: 'utf8', flag: 'wx',
    })
    await rename(temporary, target)
  } finally {
    await rm(temporary, { force: true })
  }
}
