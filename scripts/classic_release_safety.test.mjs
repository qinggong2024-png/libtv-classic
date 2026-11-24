import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const frontendPackage = fileURLToPath(new URL('../frontend/package.json', import.meta.url))
const publishScript = fileURLToPath(new URL('./publish_classic_frontend.mjs', import.meta.url))

test('npm run build stages artifacts without publishing the production HTML', () => {
  const scripts = JSON.parse(readFileSync(frontendPackage, 'utf8')).scripts
  assert.match(scripts.build, /vite build --outDir dist-build/)
  assert.doesNotMatch(scripts.build, /publish_classic_frontend|frontend\/dist(?:\\s|$)/)
})

test('frontend publisher requires an explicit operator apply action', () => {
  const result = spawnSync(process.execPath, [publishScript], { encoding: 'utf8', timeout: 4000 })
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /requires explicit --apply/)
})
