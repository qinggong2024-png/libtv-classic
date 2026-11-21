import assert from 'node:assert/strict'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { startClassicServer } from './classic_web_server.mjs'
import { publishClassicFrontend } from './publish_classic_frontend.mjs'

async function makeBuild(root, name) {
  await mkdir(join(root, 'assets'), { recursive: true })
  await writeFile(join(root, 'index.html'), `<html><script type="module" src="/assets/index-${name}.js"></script></html>`)
  await writeFile(join(root, 'assets', `index-${name}.js`), `/* ${name} */`)
}

test('publish switches HTML only after assets exist and retains prior chunks for open tabs', async () => {
  const temp = await mkdtemp(join(tmpdir(), 'classic-atomic-publish-'))
  const live = join(temp, 'live')
  let server
  try {
    await makeBuild(join(temp, 'first'), 'first')
    await publishClassicFrontend({ stageDir: join(temp, 'first'), distDir: live })
    server = await startClassicServer({ root: live, hosts: ['127.0.0.1'], port: 0, apiPort: 1 })
    const origin = `http://127.0.0.1:${server.port}`
    const oldPage = await (await fetch(origin)).text()
    assert.match(oldPage, /index-first.js/)
    await makeBuild(join(temp, 'second'), 'second')
    await publishClassicFrontend({ stageDir: join(temp, 'second'), distDir: live })
    const newPage = await (await fetch(origin)).text()
    assert.match(newPage, /index-second.js/)
    assert.equal((await fetch(`${origin}/assets/index-first.js`)).status, 200)
    assert.equal((await fetch(`${origin}/assets/index-second.js`)).status, 200)
    assert.equal((await fetch(origin)).headers.get('cache-control'), 'no-store')
    assert.match((await fetch(`${origin}/assets/index-first.js`)).headers.get('cache-control'), /immutable/)

    // A failed/incomplete build must never change the live HTML.
    await mkdir(join(temp, 'incomplete'))
    await writeFile(join(temp, 'incomplete', 'index.html'), '<html>broken</html>')
    await assert.rejects(publishClassicFrontend({ stageDir: join(temp, 'incomplete'), distDir: live }),
      /assets directory missing/)
    assert.match(await (await fetch(origin)).text(), /index-second.js/)
  } finally {
    if (server) await server.close()
    await rm(temp, { recursive: true, force: true })
  }
})
