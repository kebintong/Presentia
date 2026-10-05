// Copies the face-tracking library and model into public/vendor so the site
// serves them itself: no third-party CDN, works on school networks that block
// CDNs, and no student's browser talks to anyone but this site.
import { copyFileSync, mkdirSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const web = join(here, '..')
const mp = join(web, 'node_modules', '@mediapipe', 'tasks-vision')
const out = join(web, 'public', 'vendor')
mkdirSync(join(out, 'wasm'), { recursive: true })

const files = [
  [join(mp, 'vision_bundle.mjs'), join(out, 'vision_bundle.mjs')],
  [join(mp, 'wasm', 'vision_wasm_internal.js'), join(out, 'wasm', 'vision_wasm_internal.js')],
  [join(mp, 'wasm', 'vision_wasm_internal.wasm'), join(out, 'wasm', 'vision_wasm_internal.wasm')],
  [join(mp, 'wasm', 'vision_wasm_nosimd_internal.js'), join(out, 'wasm', 'vision_wasm_nosimd_internal.js')],
  [join(mp, 'wasm', 'vision_wasm_nosimd_internal.wasm'), join(out, 'wasm', 'vision_wasm_nosimd_internal.wasm')],
  // Same model file the desktop app uses for its own liveness check.
  [join(web, '..', 'models', 'face_landmarker.task'), join(out, 'face_landmarker.task')],
]
for (const [from, to] of files) {
  if (!existsSync(from)) {
    console.error(`Missing ${from}. Run "npm install" in web/ first.`)
    process.exit(1)
  }
  copyFileSync(from, to)
}
console.log(`Copied ${files.length} files to public/vendor`)
