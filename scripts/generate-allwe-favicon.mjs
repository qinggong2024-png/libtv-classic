import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

// Embed the original PNG: SVG favicons cannot load an external image resource.
const source = new URL('../frontend/public/brand/allwe-logo.png', import.meta.url)
const target = new URL('../frontend/public/favicon.svg', import.meta.url)
const logo = await readFile(source)
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 477 477">
  <image x="16" width="1918" height="477" href="data:image/png;base64,${logo.toString('base64')}" />
</svg>
`
await writeFile(target, svg)
console.log(`Generated ${fileURLToPath(target)} from the original ALLWE logo.`)
