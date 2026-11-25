import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, mkdirSync, copyFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
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


test('direct --apply cannot write production-shaped dist without the owning release locks', () => {
  const root = mkdtempSync(join(tmpdir(), 'classic-release-owner-'))
  try {
    const script = join(root, 'scripts', 'publish_classic_frontend.mjs')
    const live = join(root, 'frontend', 'dist')
    const stage = join(root, 'frontend', 'dist-build')
    const runtime = join(root, 'var', 'classic-lan', 'runtime')
    for (const dir of [join(root, 'scripts'), live, join(stage, 'assets'), runtime]) mkdirSync(dir, { recursive: true })
    copyFileSync(publishScript, script)
    writeFileSync(join(live, 'index.html'), 'old')
    const next = '<script src="/assets/index-next.js"></script>'
    writeFileSync(join(stage, 'index.html'), next)
    writeFileSync(join(stage, 'assets', 'index-next.js'), 'next')
    const invoke = () => spawnSync(process.execPath, [script, '--apply'], { encoding: 'utf8', timeout: 4000 })
    let result = invoke()
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /requires the existing release manager locks/)
    assert.equal(readFileSync(join(live, 'index.html'), 'utf8'), 'old')
    writeFileSync(join(runtime, 'classic-release.lock'), 'pid=0 at=test')
    writeFileSync(join(runtime, 'api-routing.lock'), 'test')
    result = invoke()
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /requires the owning release manager/)
    assert.equal(readFileSync(join(live, 'index.html'), 'utf8'), 'old')
    writeFileSync(join(runtime, 'classic-release.lock'), `pid=${process.pid} at=test`)
    result = invoke()
    assert.equal(result.status, 0, result.stderr)
    assert.equal(readFileSync(join(live, 'index.html'), 'utf8'), next)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
