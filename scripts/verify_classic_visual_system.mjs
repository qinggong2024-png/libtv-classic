import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const base = resolve(fileURLToPath(new URL('..', import.meta.url)))
const css = readFileSync(resolve(base, 'frontend/src/visualSystem.css'), 'utf8')
const entry = readFileSync(resolve(base, 'frontend/src/main.tsx'), 'utf8')
const spec = readFileSync(resolve(base, 'docs/classic-visual-system-v1.md'), 'utf8')

const assert = (condition, message) => { if (!condition) throw new Error(message) }
const routes = {
  imageStudio: '.image-center',
  videoComposer: '.home-view',
  taskCenter: '.task-center-card',
  taskDetail: '.production-workspace',
  contentLibrary: '.content-library-view',
  admin: '.admin-view',
  preferences: '.settings-view',
}
assert(entry.replace(/\r\n/g, '\n').includes("import './styles.css'\nimport './visualSystem.css'"), 'Shared stylesheet must load after legacy base layer')
for (const [name, selector] of Object.entries(routes)) {
  assert(css.includes(selector), `Missing visual scope: ${name}`)
}
for (const selector of [
  ':focus-visible',
  'prefers-reduced-motion',
  '@media (max-width: 920px)',
  '@media (max-width: 540px)',
  'task-detail-primary-output',
  '--visual-space-6',
]) assert(css.includes(selector), `Missing visual-system rule: ${selector}`)
for (const phrase of ['审核通过', '已送剪成功', 'negativePrompt', 'fetch(']) {
  assert(!css.includes(phrase), `Business state or submission code must not be invented in CSS: ${phrase}`)
}
assert(spec.includes('已查看 ≠ 已审核通过'), 'Document the separation of view receipts and approval')
console.log('CLASSIC_VISUAL_SYSTEM_PASS', Object.keys(routes).length, 'screens including task detail')
