import { flushSync } from 'react-dom'

/*
 * Animated theme changes (Light / Dark / Iridescent).
 *
 * Where the webview supports View Transitions (WebView2 on Windows does), the
 * new look is revealed in a circle growing from the button that was clicked.
 * Elsewhere colours cross-fade instead. With Settings → Animations off the
 * change is instant.
 */

const DURATION = 560
const EASE = 'cubic-bezier(0.22, 1, 0.36, 1)'

// Where the user last pressed, so the reveal starts from the clicked control
// without every caller having to pass the click event through.
let lastPointer = { x: 0, y: 0, t: -Infinity }
window.addEventListener(
  'pointerdown',
  (e) => { lastPointer = { x: e.clientX, y: e.clientY, t: performance.now() } },
  { capture: true, passive: true },
)

let fadeTimer = 0

export function switchTheme(apply: () => void): void {
  const root = document.documentElement
  if (root.dataset.motion === 'off') {
    apply()
    return
  }

  const doc = document as Document & {
    startViewTransition?: (cb: () => void) => {
      ready: Promise<void>
      finished: Promise<void>
    }
  }

  if (typeof doc.startViewTransition !== 'function') {
    // Fallback: briefly let colours transition while the theme attribute flips.
    root.classList.add('theme-fading')
    apply()
    window.clearTimeout(fadeTimer)
    fadeTimer = window.setTimeout(() => root.classList.remove('theme-fading'), DURATION)
    return
  }

  const w = window.innerWidth
  const h = window.innerHeight
  const recent = performance.now() - lastPointer.t < 1500
  const x = recent ? lastPointer.x : w / 2
  const y = recent ? lastPointer.y : 0
  const radius = Math.hypot(Math.max(x, w - x), Math.max(y, h - y))

  root.classList.add('theme-reveal')
  // flushSync so React commits (and the layout effects set data-theme /
  // data-style) before the browser takes the "after" snapshot.
  const transition = doc.startViewTransition(() => flushSync(apply))
  transition.ready
    .then(() => {
      root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        { duration: DURATION, easing: EASE, pseudoElement: '::view-transition-new(root)' },
      )
    })
    .catch(() => { /* transition skipped; the theme still changed */ })
  transition.finished.finally(() => root.classList.remove('theme-reveal'))
}
