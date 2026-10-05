/*
 * Diagnostics, shared by the whole app.
 *
 * - Watches requests to the Presentia engine. When one fails (server error
 *   or no answer) or a screen throws, the problem is recorded by the engine
 *   (Settings → Diagnostics → Recent problems).
 * - In diagnostic mode, the problem is also offered to the user right away
 *   (ProblemPrompt) so they can send, copy or save a report.
 * - Builds the plain-text report, with the user's name taken out of paths.
 */
import { ClipboardSetText } from '../wailsjs/runtime/runtime'

export const API = 'http://127.0.0.1:7788'

export interface Problem {
  id: number
  time: Date
  title: string
  message: string
  path: string
  status: number
}

export interface DiagMode {
  enabled: boolean
  since: string | null
  until: string | null
  expired: boolean
  log_path: string
  /** Where reports are sent: fixed, separate from the registration website. */
  reports_url?: string
}

/** "presentia.example.workers.dev" from the report address. */
export function reportsHost(m: DiagMode = mode): string {
  try { return m.reports_url ? new URL(m.reports_url).host : 'the Presentia report service' } catch { return 'the Presentia report service' }
}

// ── diagnostic mode state ──────────────────────────────────────────────────

let mode: DiagMode = { enabled: false, since: null, until: null, expired: false, log_path: '' }
const modeListeners = new Set<(m: DiagMode) => void>()
const problemListeners = new Set<(p: Problem) => void>()

export const getMode = () => mode

export function onMode(fn: (m: DiagMode) => void): () => void {
  modeListeners.add(fn)
  return () => { modeListeners.delete(fn) }
}

export function onProblem(fn: (p: Problem) => void): () => void {
  problemListeners.add(fn)
  return () => { problemListeners.delete(fn) }
}

function setMode(m: DiagMode) {
  mode = m
  modeListeners.forEach((fn) => fn(m))
}

let realFetch: typeof fetch = (...args) => window.fetch(...args)

export async function refreshMode(): Promise<DiagMode | null> {
  try {
    const res = await realFetch(`${API}/api/diagnostics/mode`)
    if (!res.ok) return null
    const m: DiagMode = await res.json()
    setMode(m)
    return m
  } catch {
    return null
  }
}

export async function setDiagnosticMode(enabled: boolean): Promise<DiagMode> {
  const res = await realFetch(`${API}/api/diagnostics/mode`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const m: DiagMode = await res.json()
  setMode(m)
  return m
}

// ── catching problems ──────────────────────────────────────────────────────

/** What the user was doing, from the request that failed. */
const ACTIONS: [RegExp, string, string][] = [
  [/\/api\/classes\/\d+\/online$/, 'PUT', 'Turning online registration on or off'],
  [/\/api\/classes\/\d+\/sync$/, '*', 'Checking for new registrations'],
  [/\/api\/cloud$/, 'PUT', 'Connecting to the registration website'],
  [/\/api\/pending\/\d+\/approve$/, '*', 'Accepting a registration'],
  [/\/api\/pending\/\d+$/, 'DELETE', 'Rejecting a registration'],
  [/\/export\.xlsx$/, '*', 'Exporting to Excel'],
  [/\/export-csv$/, '*', 'Exporting attendance'],
  [/\/api\/enroll\//, '*', 'Reading faces from photos'],
  [/\/api\/students/, '*', 'Saving student details'],
  [/\/api\/classes/, '*', 'Updating the class'],
  [/\/api\/sessions|\/api\/attendance/, '*', 'Recording attendance'],
  [/\/api\/perf/, '*', 'Changing performance settings'],
  [/\/api\/checks/, '*', 'Changing check-in settings'],
]

function actionFor(path: string, method: string): string {
  for (const [re, m, label] of ACTIONS) {
    if (re.test(path) && (m === '*' || m === method)) return label
  }
  return 'Talking to the Presentia engine'
}

/** Polled constantly or expected to fail while starting; never prompted. */
const QUIET = ['/api/engine/status', '/api/monitor/', '/api/diagnostics', '/api/screen/screenshot']
const REPEAT_MS = 120_000

let nextId = 1
const lastShown = new Map<string, number>()
let engineSeen = false

function report(p: Omit<Problem, 'id' | 'time'>) {
  // Always recorded by the engine (recent problems, and the log in diagnostic mode).
  realFetch(`${API}/api/diagnostics/event`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: 'error', ...p }),
  }).catch(() => { /* engine down: nothing to record to */ })

  if (!mode.enabled) return
  const key = `${p.title}|${p.message}`
  const now = Date.now()
  if (now - (lastShown.get(key) || 0) < REPEAT_MS) return
  lastShown.set(key, now)
  const problem: Problem = { ...p, id: nextId++, time: new Date() }
  problemListeners.forEach((fn) => fn(problem))
}

let installed = false

/** Call once, before the app renders. */
export function installDiagnostics() {
  if (installed) return
  installed = true
  const original = window.fetch.bind(window)
  realFetch = original

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (!url.startsWith(API)) return original(input, init)
    const path = new URL(url).pathname
    const quiet = QUIET.some((q) => path.startsWith(q))
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase()
    let res: Response
    try {
      res = await original(input, init)
    } catch (err) {
      if (!quiet && engineSeen) {
        report({
          title: actionFor(path, method),
          message: 'The Presentia engine did not answer. It may have stopped or be restarting.',
          path, status: 0,
        })
      }
      throw err
    }
    engineSeen = true
    // 503 = still loading the face models; the screens already say so.
    if (!quiet && res.status >= 500 && res.status !== 503) {
      let message = `The engine answered with an error (HTTP ${res.status}).`
      try {
        const body = await res.clone().json()
        const d = body?.detail
        message = typeof d === 'string' ? d : d?.message || message
      } catch { /* not JSON */ }
      report({ title: actionFor(path, method), message, path, status: res.status })
    }
    return res
  }

  window.addEventListener('error', (e) => {
    const msg = String(e.message || '')
    if (!msg || msg.includes('ResizeObserver')) return
    report({ title: 'A screen ran into an error', message: msg, path: e.filename ? `${e.filename}:${e.lineno}` : '', status: 0 })
  })
  window.addEventListener('unhandledrejection', (e) => {
    const reason: any = e.reason
    const msg = String(reason?.message || reason || '')
    if (!msg || /Failed to fetch|NetworkError|Load failed/i.test(msg)) return  // already reported by the fetch hook
    report({ title: 'A screen ran into an error', message: msg, path: '', status: 0 })
  })

  // Learn whether diagnostic mode is on as soon as the engine answers, then
  // check now and then (it turns itself off after a week).
  const poll = async () => {
    const ok = await refreshMode()
    setTimeout(poll, ok ? 5 * 60_000 : 3_000)
  }
  poll()
}

// ── reports ────────────────────────────────────────────────────────────────

type Status = 'ok' | 'warn' | 'fail' | 'skip'

export interface Check {
  id: string
  title: string
  status: Status
  detail: string
  ms: number
}

export interface DiagnosticsData {
  generated_at: string
  system: { label: string; value: string }[]
  network: { url: string; checks: Check[]; hints: string[]; trust_in_use: string | null } | null
  recent: { time: string; source: string; level: string; message: string }[]
  log: string
  mode: DiagMode
}

export const STATUS_LABEL: Record<Status, string> = { ok: 'OK', warn: 'Note', fail: 'Failed', skip: 'Skipped' }

/** Problems with the registration website deserve the connection checks. */
export const needsNetworkCheck = (p: Problem | null) =>
  !p || /online|sync|cloud|pending|website/i.test(`${p.path} ${p.message}`)

export async function fetchDiagnostics(network: boolean): Promise<DiagnosticsData> {
  const res = await realFetch(`${API}/api/diagnostics?network=${network}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchActivityLog(lines = 400): Promise<{ text: string; path: string; size: number; enabled: boolean }> {
  const res = await realFetch(`${API}/api/diagnostics/log?lines=${lines}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** Replace the Windows/Linux user name found in paths, everywhere. */
export function redact(text: string): string {
  const names = new Set<string>()
  for (const m of text.matchAll(/[A-Za-z]:\\(?:Users|Documents and Settings)\\([^\\\r\n"'<>]+)/g)) names.add(m[1])
  for (const m of text.matchAll(/\/(?:home|Users)\/([^/\s"'<>]+)/g)) names.add(m[1])
  let out = text
  for (const name of names) {
    if (name.length < 2 || /^(public|default|shared)$/i.test(name)) continue
    out = out.split(name).join('<you>')
  }
  return out
}

export function reportText(d: DiagnosticsData, version: string, problem: Problem | null, activity: string): string {
  const out: string[] = []
  out.push(`Presentia diagnostic report — ${d.generated_at}`)
  if (problem) {
    out.push('', `Problem: ${problem.title} failed`, `Message: ${problem.message}`)
    if (problem.path) out.push(`Request: ${problem.path}${problem.status ? ` (HTTP ${problem.status})` : ''}`)
    out.push(`When: ${problem.time.toLocaleString()}`)
  }
  out.push('', `App version: ${version || 'development'}`)
  out.push(`Diagnostic mode: ${d.mode.enabled ? `on since ${d.mode.since}` : 'off'}`)
  out.push(`Webview: ${navigator.userAgent}`)
  for (const s of d.system) out.push(`${s.label}: ${s.value}`)
  if (d.network) {
    out.push('', `Registration website: ${d.network.url || '(not set)'}`)
    for (const c of d.network.checks) {
      out.push(`[${STATUS_LABEL[c.status]}] ${c.title}${c.ms ? ` (${c.ms} ms)` : ''}: ${c.detail}`)
    }
    if (d.network.trust_in_use) out.push(`Certificate check in use: ${d.network.trust_in_use}`)
    if (d.network.hints.length) {
      out.push('', 'What to do:')
      for (const h of d.network.hints) out.push(`- ${h}`)
    }
  }
  out.push('', 'Recent problems:')
  if (d.recent.length === 0) out.push('(none since Presentia started)')
  for (const e of d.recent) out.push(`${e.time} [${e.level}] ${e.source}: ${e.message}`)
  if (activity.trim()) out.push('', 'Activity log (last lines):', activity.trimEnd())
  if (d.log.trim()) out.push('', 'Engine log (last lines):', d.log.trimEnd())
  return redact(out.join('\n'))
}

/** The full report for a problem (or for "Report a problem" with none). */
export async function buildReport(version: string, problem: Problem | null): Promise<string> {
  const [d, act] = await Promise.all([
    fetchDiagnostics(needsNetworkCheck(problem)),
    fetchActivityLog(300).catch(() => ({ text: '' })),
  ])
  return reportText(d, version, problem, act.text)
}

export function reportSummary(problem: Problem | null): string {
  return problem ? `${problem.title} failed: ${problem.message}`.slice(0, 200) : 'Problem reported from Settings'
}

export async function sendReport(summary: string, text: string, version: string): Promise<{ id: string; github: boolean }> {
  const res = await realFetch(`${API}/api/diagnostics/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ summary, text, app_version: version }),
  })
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const d = body?.detail
    throw new Error(typeof d === 'string' ? d : d?.message || `Could not send (HTTP ${res.status}).`)
  }
  return body
}

export async function copyText(text: string): Promise<boolean> {
  try {
    if ((window as any)['runtime']) return await ClipboardSetText(text)
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** Native "Save as" in the app; a download in a plain browser. Returns
 *  where it went, or null if the user cancelled. */
export async function saveText(defaultName: string, text: string): Promise<string | null> {
  const save = (window as any)['go']?.['main']?.['App']?.['SaveTextToFile']
  if (save) {
    const path: string = await save(defaultName, text)
    return path || null
  }
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
  a.download = defaultName
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  return 'your Downloads folder'
}

export function reportFileName(prefix = 'presentia-report'): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${prefix}-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.txt`
}
