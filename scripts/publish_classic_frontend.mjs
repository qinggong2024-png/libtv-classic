/**
 * Publish an already-built Vite bundle without breaking tabs running an older version.
 * All new assets are copied before the entry HTML is atomically replaced.
 * Hashed assets from previous builds are deliberately retained.
 */
import { cp, mkdir, readFile, readdir, rename, rm, writeFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { spawn } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

export async function publishClassicFrontend({
  stageDir = join(projectRoot, 'frontend', 'dist-stage'),
  distDir = join(projectRoot, 'frontend', 'dist'),
} = {}) {
  const stage = resolve(stageDir)
  const destination = resolve(distDir)
  if (stage === destination || destination.startsWith(stage + '\\') || destination.startsWith(stage + '/')) {
    throw new Error('Staging and live directories must be separate')
  }

  // Verify the build is complete before modifying any live file.
  const index = await readFile(join(stage, 'index.html'))
  const entries = await readdir(stage, { withFileTypes: true })
  if (!entries.some(entry => entry.isDirectory() && entry.name === 'assets')) {
    throw new Error('Incomplete frontend build: assets directory missing')
  }
  if ((await readdir(join(stage, 'assets'))).length === 0) {
    throw new Error('Incomplete frontend build: assets directory empty')
  }

  await mkdir(destination, { recursive: true })
  for (const entry of entries) {
    if (entry.name === 'index.html') continue
    await cp(join(stage, entry.name), join(destination, entry.name), {
      recursive: true,
      // Hashed assets are immutable. Do not rewrite a file a browser might be reading.
      force: entry.name !== 'assets',
      errorOnExist: false,
    })
  }

  // Existing tabs continue to resolve their immutable, content-hashed JS chunks.
  // New visitors see only an HTML entry whose entire asset set is now present.
  const pendingIndex = join(destination, `.index-${randomUUID()}.tmp`)
  try {
    await writeFile(pendingIndex, index, { flag: 'wx' })
    await rename(pendingIndex, join(destination, 'index.html'))
  } finally {
    await rm(pendingIndex, { force: true })
  }
  await rm(stage, { recursive: true, force: true })
}

async function buildAndPublish() {
  const frontendRoot = join(projectRoot, 'frontend')
  const stageDir = join(frontendRoot, `.dist-stage-${randomUUID()}`)
  const viteCli = join(frontendRoot, 'node_modules', 'vite', 'bin', 'vite.js')
  try {
    await new Promise((accept, reject) => {
      const worker = spawn(process.execPath, [viteCli, 'build', '--outDir', stageDir], {
        cwd: frontendRoot,
        stdio: 'inherit',
      })
      worker.once('error', reject)
      worker.once('close', code => code === 0 ? accept() : reject(new Error(`Vite build failed: ${code}`)))
    })
    await publishClassicFrontend({ stageDir })
  } finally {
    await rm(stageDir, { recursive: true, force: true })
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await buildAndPublish()
  console.log('Classic frontend published: previous hashed assets retained')
}
