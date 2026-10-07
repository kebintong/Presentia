/*
 * The six looks in Settings → Appearance.
 *
 * Each theme is a mode (light or dark, which picks the base tokens in
 * style.css via <html data-theme>) plus an optional style layered on top
 * (<html data-style>). Normal Light and Normal Dark have no style.
 */

export type ThemeKey = 'light' | 'brutal' | 'editorial' | 'bento' | 'iri' | 'dark'
export type Mode = 'light' | 'dark'

export interface ThemeInfo {
  key: ThemeKey
  name: string
  mode: Mode
  /** Value for <html data-style>, if the theme has one. */
  style?: string
  tag?: 'New' | 'Beta'
}

// Order matters: this is the 2 × 3 grid in Settings, left to right.
export const THEMES: ThemeInfo[] = [
  { key: 'light',     name: 'Normal Light',   mode: 'light' },
  { key: 'brutal',    name: 'New Brutalism',  mode: 'light', style: 'brutal',     tag: 'New' },
  { key: 'editorial', name: 'Editorial Grid', mode: 'light', style: 'editorial',  tag: 'New' },
  { key: 'bento',     name: 'Soft Bento',     mode: 'light', style: 'bento',      tag: 'New' },
  { key: 'iri',       name: 'Iridescent',     mode: 'dark',  style: 'iridescent', tag: 'Beta' },
  { key: 'dark',      name: 'Normal Dark',    mode: 'dark' },
]

export const themeInfo = (key: ThemeKey): ThemeInfo =>
  THEMES.find((t) => t.key === key) ?? THEMES[THEMES.length - 1]

const isKey = (v: unknown): v is ThemeKey => THEMES.some((t) => t.key === v)

const THEME_KEY = 'presentia-theme'
const OLD_IRIDESCENT = 'presentia-iridescent'

function read(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}
function write(key: string, value: string) {
  try { localStorage.setItem(key, value) } catch { /* storage unavailable */ }
}

/** The saved theme. Settings from before 1.6 (light/dark plus an Iridescent switch) carry over. */
export function loadTheme(): ThemeKey {
  if (read(OLD_IRIDESCENT) === '1') return 'iri'
  const saved = read(THEME_KEY)
  return isKey(saved) ? saved : 'dark'
}

export function saveTheme(key: ThemeKey) {
  write(THEME_KEY, key)
  try { localStorage.removeItem(OLD_IRIDESCENT) } catch { /* ignore */ }
}
