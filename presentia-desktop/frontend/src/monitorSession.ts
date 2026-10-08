// The running Meeting Monitor, kept outside the Monitor page.
//
// The monitor used to live inside MeetPage: leaving the page (to look at
// Students, say) closed its connection, which stopped monitoring in the
// middle of a class. Now the connection and what it reports (roster, unknown
// faces, activity, the live picture) live here for as long as the app runs;
// the page only shows them. Monitoring stops when the instructor presses
// Stop, or when they switch to another class.

import { useSyncExternalStore } from 'react'
import { makeAlert } from './components/AlertList'
import { createFrameFeed, type Frame } from './components/frameFeed'
import { EventsOn } from '../wailsjs/runtime/runtime'

const WS = 'ws://127.0.0.1:7788'

export interface AlertItem {
  id: number
  ts: string
  message: string
  level: 'ok' | 'warn' | 'error' | 'info'
}

export interface MonitorState {
  monitoring: boolean
  connecting: boolean
  sessionId: number | null
  classId: number | null
  /** How the selected window is being captured, and whether that is paused. */
  capture: { state: string; method: string; browser?: boolean; title?: string } | null
  roster: any[]
  unknowns: any[]
  alerts: AlertItem[]
  hasFrame: boolean
}

const initial: MonitorState = {
  monitoring: false, connecting: false, sessionId: null, classId: null, capture: null,
  roster: [], unknowns: [], alerts: [], hasFrame: false,
}

let state: MonitorState = initial
const listeners = new Set<() => void>()
let ws: WebSocket | null = null
let extra: ((data: any) => void) | null = null
let lastConnError = 0

/** Live frames go straight to the canvases (see frameFeed), not through React. */
export const monitorFeed = createFrameFeed()

function set(patch: Partial<MonitorState>) {
  state = { ...state, ...patch }
  listeners.forEach((fn) => fn())
}

function setFrame(f: Frame) {
  monitorFeed.push(f)
  if ((f !== null) !== state.hasFrame) set({ hasFrame: f !== null })
}

export function addMonitorAlert(message: string, level: AlertItem['level'] = 'info') {
  set({ alerts: [makeAlert(message, level), ...state.alerts].slice(0, 100) })
}

export function clearMonitorAlerts() { set({ alerts: [] }) }

export function getMonitorState(): MonitorState { return state }

export function subscribeMonitor(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** React hook: the monitor's current state. */
export function useMonitor(): MonitorState {
  return useSyncExternalStore(subscribeMonitor, getMonitorState, getMonitorState)
}

/** The Monitor page's handler for messages only it shows (liveness check,
 *  re-verify prompt). Returns the unregister function. */
export function setMonitorPageHandler(fn: (data: any) => void): () => void {
  extra = fn
  return () => { if (extra === fn) extra = null }
}

const unknownList = (list: any[] | undefined) => (list || []).map((u: any, i: number) => ({ ...u, index: i }))

/** Starts monitoring. `start` is the sidecar's start message (region, name…). */
export function startMonitor(start: Record<string, unknown> & { class_id?: number | null }, label: string) {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    addMonitorAlert('Monitoring is already running.', 'info')
    return
  }
  const sock = new WebSocket(`${WS}/ws/screen`)
  sock.binaryType = 'arraybuffer' // preview frames arrive as raw JPEG bytes
  ws = sock
  set({ connecting: true, classId: (start.class_id as number | null) ?? null })
  sock.onopen = () => sock.send(JSON.stringify({ action: 'start', ...start }))
  sock.onmessage = (ev) => {
    if (typeof ev.data !== 'string') {
      setFrame(ev.data as ArrayBuffer)
      return
    }
    let data: any
    try { data = JSON.parse(ev.data) } catch { return }
    switch (data.type) {
      case 'started':
        set({ sessionId: data.session_id, monitoring: true, connecting: false })
        addMonitorAlert(`Monitoring started: "${label}"`, 'ok')
        break
      case 'frame':
        // Older sidecars: the picture (base64) and roster in one message.
        if (data.jpeg) setFrame(data.jpeg)
        if (data.roster) set({ roster: data.roster })
        if (data.unknowns) set({ unknowns: unknownList(data.unknowns) })
        break
      case 'analysis':
        set({ roster: data.roster || [], unknowns: unknownList(data.unknowns) })
        break
      case 'alert':
        addMonitorAlert(data.message, data.level)
        break
      case 'capture':
        set({ capture: { state: data.state, method: data.method, browser: !!data.browser, title: data.title } })
        break
      case 'enrolled':
        addMonitorAlert(`${data.name} enrolled from meeting.`, 'ok')
        break
      case 'verify_result':
        addMonitorAlert(data.message, data.ok ? 'ok' : 'error')
        break
      case 'stopped':
        set({ monitoring: false, connecting: false, sessionId: null, capture: null })
        setFrame(null)
        addMonitorAlert('Monitoring stopped. Attendance recorded.', 'info')
        sock.close()
        break
      case 'error':
        addMonitorAlert(`Error: ${data.message}`, 'error')
        break
    }
    extra?.(data)
  }
  sock.onclose = () => {
    if (ws === sock) ws = null
    const wasRunning = state.monitoring
    set({ monitoring: false, connecting: false, sessionId: null, capture: null })
    setFrame(null)
    if (wasRunning) {
      addMonitorAlert('Lost the connection to the Presentia engine — monitoring stopped. '
        + 'If it does not come back by itself, restart Presentia.', 'error')
    }
  }
  sock.onerror = () => {
    // One message, not one per retry.
    const now = Date.now()
    if (now - lastConnError > 60_000) {
      lastConnError = now
      addMonitorAlert('Could not reach the Presentia engine.', 'error')
    }
  }
}

/** Sends a message to the running monitor; false if it is not connected. */
export function sendMonitor(payload: Record<string, unknown>): boolean {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload))
    return true
  }
  return false
}

/** Stops monitoring (attendance is recorded by the engine). */
export function stopMonitor() {
  if (!sendMonitor({ action: 'stop' })) {
    try { ws?.close() } catch { /* ignore */ }
    ws = null
    set({ monitoring: false, connecting: false, sessionId: null, capture: null })
    setFrame(null)
  }
}

/** The desktop shell restarts the engine if it stops unexpectedly; say so
 *  where the instructor looks. Returns the unsubscribe function. */
export function listenEngineEvents(): () => void {
  const offs: Array<() => void> = []
  try {
    offs.push(EventsOn('sidecar:restarted', () => {
      addMonitorAlert('The Presentia engine stopped unexpectedly and was restarted. '
        + 'Press Start Monitoring to continue.', 'error')
    }))
    offs.push(EventsOn('sidecar:failed', () => {
      addMonitorAlert('The Presentia engine keeps stopping. Please restart Presentia, and turn on '
        + 'Settings → Diagnostics if it happens again.', 'error')
    }))
  } catch { /* browser preview */ }
  return () => offs.forEach((off) => { try { off() } catch { /* ignore */ } })
}

/** For tests. */
export function _resetMonitor() {
  try { ws?.close() } catch { /* ignore */ }
  ws = null
  extra = null
  state = initial
  listeners.forEach((fn) => fn())
}
