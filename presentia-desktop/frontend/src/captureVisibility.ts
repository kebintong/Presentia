// "Hide Presentia from screen recordings" (Settings → Appearance).
//
// Off by default: Presentia's windows, the floating bubble and Live View
// included, show in screenshots, recordings and screen sharing like any other
// app. On: Windows leaves them out of every capture (the native side applies
// WDA_EXCLUDEFROMCAPTURE; see capture_win.go).

const KEY = 'presentia-hide-from-capture'

export function loadHideFromCapture(): boolean {
  try { return localStorage.getItem(KEY) === 'on' } catch { return false }
}

export function applyHideFromCapture(hide: boolean): void {
  try { localStorage.setItem(KEY, hide ? 'on' : 'off') } catch { /* storage unavailable */ }
  try { (window as any)['go']?.['main']?.['App']?.['SetHideFromCapture']?.(hide)?.catch?.(() => {}) } catch { /* not in the desktop shell */ }
}
