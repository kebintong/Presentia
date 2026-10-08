import React, { useState, useEffect, useRef, useCallback } from 'react'
import AlertList, { makeAlert } from '../components/AlertList'
import RosterList from '../components/RosterList'
import VideoCanvas from '../components/VideoCanvas'
import {
  GetOpenWindows,
  PrepareScreenPick, EnterPickerMode, ExitPickerMode,
  OpenBubble, CloseBubble,
} from '../../wailsjs/go/main/App'
import { setBubbleHandler, rememberSource, rememberedSource, tellBubbleSource, type BubbleCmd } from '../bubbleBridge'
import { useFrameFeed, type Frame } from '../components/frameFeed'
import { ClassInfo, classLabel } from '../classes'
import VerifyDialog, { CheckState, VerifyStudent, newCheck } from '../components/VerifyDialog'

const API = 'http://127.0.0.1:7788'
// Bindings added after the generated wailsjs files; called defensively.
const goApp = () => (window as any)['go']?.['main']?.['App']
const WS  = 'ws://127.0.0.1:7788'

type RosterStudent = VerifyStudent

interface AlertItem {
  id: number
  ts: string
  message: string
  level: 'ok' | 'warn' | 'error' | 'info'
}

interface UnknownFace {
  uid?: number
  crop_jpeg: string
  bbox: number[]
  index: number
}

interface EnrollDialog {
  unknown: UnknownFace
  studentNo: string
  name: string
}

interface WindowInfo {
  title: string
  left: number
  top: number
  width: number
  height: number
  hwnd?: number
}

/** What to monitor: a fixed screen area, or a window (followed as it moves). */
interface Region {
  left: number
  top: number
  width: number
  height: number
  hwnd?: number
  title?: string
}

/** What is remembered between visits to the page. */
interface Source {
  region: Region
  thumb: string | null
}

interface ScreenShot {
  jpeg: string
  width: number
  height: number
  left: number
  top: number
}

// Capture states in which monitoring waits (see _WindowFollower in sidecar.py).
const PAUSED_CAPTURE: Record<string, string> = {
  minimized: 'window minimised',
  closed: 'window closed',
  hidden: 'window fully covered',
  covered: 'window covered',
}

// Crop the picked part of the picker's screenshot into a small JPEG
// (fractions of the picture, so the screenshot's own scale does not matter).
function makeThumb(jpeg: string, fx: number, fy: number, fw: number, fh: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const sw = Math.max(1, fw * img.naturalWidth), sh = Math.max(1, fh * img.naturalHeight)
      const k = Math.min(1, 320 / sw, 180 / sh)
      const c = document.createElement('canvas')
      c.width = Math.max(1, Math.round(sw * k)); c.height = Math.max(1, Math.round(sh * k))
      const ctx = c.getContext('2d')
      if (!ctx) { reject(new Error('no canvas')); return }
      ctx.drawImage(img, fx * img.naturalWidth, fy * img.naturalHeight, sw, sh, 0, 0, c.width, c.height)
      resolve(c.toDataURL('image/jpeg', 0.8))
    }
    img.onerror = () => reject(new Error('thumbnail'))
    img.src = `data:image/jpeg;base64,${jpeg}`
  })
}

// Tell the native side what was picked; it shows a Windows notification when
// the app is going straight back to the tray (picked from the bubble).
const sourcePicked = (text: string) => {
  try { (window as any)['go']?.['main']?.['App']?.['SourcePicked']?.(text)?.catch?.(() => {}) } catch { /* browser preview */ }
}

export default function MeetPage({ classInfo }: { classInfo: ClassInfo }) {
  const [sessionName, setSessionName]   = useState('')
  const [missingAfter, setMissingAfter] = useState(5)
  const [monitoring, setMonitoring]     = useState(false)
  // How the selected window is being captured, and whether that is paused.
  const [capture, setCapture]           = useState<{ state: string; method: string } | null>(null)
  const [sessionId, setSessionId]       = useState<number | null>(null)
  // Live frames go straight from the socket to the canvas (see frameFeed);
  // React only tracks whether there is a picture at all.
  const feed = useFrameFeed()
  const [hasFrame, setHasFrame]         = useState(false)
  const setFrame = useCallback((f: Frame) => {
    feed.push(f)
    setHasFrame(f !== null)
  }, [feed])
  // What to watch survives leaving the page (bubbleBridge.ts).
  const [region, setRegion]             = useState<Region | null>(() => rememberedSource<Source>()?.region ?? null)
  // A small picture of the picked screen area, so it is plain what is watched.
  const [regionThumb, setRegionThumb]   = useState<string | null>(() => rememberedSource<Source>()?.thumb ?? null)
  const [roster, setRoster]             = useState<RosterStudent[]>([])
  const [unknowns, setUnknowns]         = useState<UnknownFace[]>([])
  const [alerts, setAlerts]             = useState<AlertItem[]>([])
  const [enrollDialog, setEnrollDialog] = useState<EnrollDialog | null>(null)
  const [verifyingId, setVerifyingId]   = useState<number | null>(null)
  const [verifyPrompt, setVerifyPrompt] = useState('')
  // Verify dialog for one student (liveness check / quick re-check).
  const [check, setCheck]               = useState<CheckState | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  // Native bubble state
  const [bubbleOpen, setBubbleOpen]   = useState(false)
  const [pickLoading, setPickLoading] = useState(false)

  // Win picker dialog
  const [showWinPicker, setShowWinPicker] = useState(false)
  const [openWindows, setOpenWindows]     = useState<WindowInfo[]>([])

  // In-app screen picker overlay state
  const [screenshot, setScreenshot]   = useState<ScreenShot | null>(null)
  const [picking, setPicking]         = useState(false)
  const [selRect, setSelRect]         = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const dragStart = useRef<{ x: number; y: number } | null>(null)
  const overlayRef = useRef<HTMLDivElement>(null)

  const addAlert = (message: string, level: AlertItem['level']) => {
    setAlerts((prev) => [makeAlert(message, level), ...prev].slice(0, 100))
  }

  // ── Native bubble ─────────────────────────────────────────────────
  // The bubble's buttons arrive through App (bubbleBridge.ts), which opens
  // this page first if needed. The ref always holds the latest handlers.
  const bubbleCmdRef = useRef<(cmd: BubbleCmd) => void>(() => {})
  bubbleCmdRef.current = (cmd) => {
    switch (cmd) {
      case 'screen_area': pickRegionFn(); break
      case 'win_picker':  openWinPickerFn(); break
      case 'launch':      startMonitoringFn(); break
      case 'stop':        stopMonitoring(); break
      case 'quit':        CloseBubble(); setBubbleOpen(false); break
      // The bubble is a native Win32 window; if it cannot be created the
      // button must not stay stuck on "Close Bubble" with nothing on screen.
      case 'failed':
        setBubbleOpen(false)
        addAlert('The floating bubble could not open — use the buttons above instead.', 'error')
        break
    }
  }
  useEffect(() => setBubbleHandler((cmd) => bubbleCmdRef.current(cmd)), [])

  // The bubble outlives this page: ask whether it is up.
  useEffect(() => {
    try { goApp()?.['BubbleIsOpen']?.()?.then?.((open: boolean) => setBubbleOpen(!!open))?.catch?.(() => {}) } catch { /* preview */ }
  }, [])

  // Keep the bubble told what will be watched, so it can show "Ready" and
  // offer Start even with the app in the tray.
  useEffect(() => {
    rememberSource<Source>(region ? { region, thumb: regionThumb } : null)
    const cls = classLabel(classInfo)
    if (!region) tellBubbleSource('', '', '', cls)
    else if (region.hwnd) tellBubbleSource('window', region.title || 'Selected window', 'Followed when it moves or is covered', cls)
    else tellBubbleSource('area', 'Screen area', `${region.width} × ${region.height} px at ${region.left}, ${region.top}`, cls)
  }, [region, regionThumb, classInfo])

  // ── Screen region picker ──────────────────────────────────────────
  const pickRegionFn = async () => {
    setPickLoading(true)
    try {
      await PrepareScreenPick()
      await new Promise(r => setTimeout(r, 380))
      const res = await fetch(`${API}/api/screen/screenshot`)
      if (!res.ok) throw new Error('Screenshot API returned ' + res.status)
      const data: ScreenShot = await res.json()
      await EnterPickerMode()
      await new Promise(r => setTimeout(r, 120))
      setScreenshot(data)
      setSelRect(null)
      setPicking(true)
    } catch (e: any) {
      addAlert(`Screen capture failed: ${e.message}`, 'error')
      try { await ExitPickerMode() } catch {}
    } finally {
      setPickLoading(false)
    }
  }

  // Leave the picker without choosing an area (ESC, right-click, Cancel).
  const cancelPick = async () => {
    dragStart.current = null
    setPicking(false)
    setScreenshot(null)
    setSelRect(null)
    try { await ExitPickerMode() } catch {}
  }

  // ESC is listened for on the whole window, not on the overlay: a <div> never
  // takes focus from React's autoFocus, and the drag's preventDefault() stops a
  // click from focusing it, so a key handler on the overlay never fired.
  useEffect(() => {
    if (!picking) return
    window.focus()
    overlayRef.current?.focus({ preventScroll: true })
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === 'Esc') {
        e.preventDefault()
        e.stopPropagation()
        cancelPick()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [picking])

  const onPickMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return // right-click cancels (onContextMenu)
    e.preventDefault()
    dragStart.current = { x: e.clientX, y: e.clientY }
    setSelRect({ x: e.clientX, y: e.clientY, w: 0, h: 0 })
  }

  const onPickMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!dragStart.current) return
    setSelRect({
      x: Math.min(e.clientX, dragStart.current.x),
      y: Math.min(e.clientY, dragStart.current.y),
      w: Math.abs(e.clientX - dragStart.current.x),
      h: Math.abs(e.clientY - dragStart.current.y),
    })
  }

  const onPickMouseUp = async (e: React.MouseEvent<HTMLDivElement>) => {
    if (!dragStart.current || !screenshot || !selRect || selRect.w < 10 || selRect.h < 10) {
      dragStart.current = null
      if (selRect && selRect.w < 10) await cancelPick()
      return
    }
    const vw = overlayRef.current?.clientWidth  || window.innerWidth
    const vh = overlayRef.current?.clientHeight || window.innerHeight
    const scaleX = screenshot.width  / vw
    const scaleY = screenshot.height / vh
    const region = {
      left:   Math.round(selRect.x * scaleX) + screenshot.left,
      top:    Math.round(selRect.y * scaleY) + screenshot.top,
      width:  Math.round(selRect.w * scaleX),
      height: Math.round(selRect.h * scaleY),
    }
    const fx = selRect.x / vw, fy = selRect.y / vh, fw = selRect.w / vw, fh = selRect.h / vh
    const shotJpeg = screenshot.jpeg
    dragStart.current = null
    setPicking(false)
    setScreenshot(null)
    setSelRect(null)
    // Picked from the bubble, the window goes back to the tray right away:
    // say what was picked there too (a Windows notification).
    sourcePicked(`Screen area selected (${region.width} × ${region.height}). Press Start on the bubble or in Presentia.`)
    try { await ExitPickerMode() } catch {}
    setRegion(region)
    setRegionThumb(null)
    makeThumb(shotJpeg, fx, fy, fw, fh).then(setRegionThumb).catch(() => {})
    addAlert(`Screen area selected: ${region.width}×${region.height}`, 'info')
  }

  const clearSource = () => { setRegion(null); setRegionThumb(null) }

  // ── Windows Tab picker ────────────────────────────────────────────
  const openWinPickerFn = async () => {
    try {
      const wins = await GetOpenWindows()
      setOpenWindows(wins || [])
    } catch {
      setOpenWindows([])
    }
    setShowWinPicker(true)
  }

  // In bubble mode the app sits in the tray; it was brought out for the
  // picker and goes back once the instructor has chosen (or cancelled).
  const closeWinPicker = () => {
    setShowWinPicker(false)
    goApp()?.['BubbleTaskDone']?.()
  }

  const selectWindow = (w: WindowInfo) => {
    // The window handle lets the sidecar follow the window if it is moved
    // or resized while monitoring.
    setRegion({ left: w.left, top: w.top, width: w.width, height: w.height, hwnd: w.hwnd, title: w.title })
    setRegionThumb(null)
    sourcePicked(`Window selected: "${w.title}". Press Start on the bubble or in Presentia.`)
    addAlert(`Window selected: "${w.title}" — it stays monitored when moved or covered by other windows (not when minimised).`, 'info')
    closeWinPicker()
  }

  // ── Monitoring ────────────────────────────────────────────────────
  const startMonitoringFn = useCallback(() => {
    if (!region) {
      // Started from the bubble with the app in the tray: bring it out so
      // the instructor actually sees why nothing happened.
      goApp()?.['ShowMainWindow']?.()
      addAlert('Select a screen area or window first — use the bubble menu.', 'warn')
      return
    }
    const ws = new WebSocket(`${WS}/ws/screen`)
    ws.binaryType = 'arraybuffer' // preview frames arrive as raw JPEG bytes
    wsRef.current = ws
    ws.onopen = () => {
      const name = sessionName.trim() || `Meet ${new Date().toLocaleString()}`
      // class_id: only this class's roster is matched and the session is
      // filed under it.
      ws.send(JSON.stringify({
        action: 'start', region, name, missing_after: missingAfter, class_id: classInfo.id,
      }))
    }
    ws.onmessage = (ev) => {
      if (typeof ev.data !== 'string') {
        setFrame(ev.data as ArrayBuffer)
        return
      }
      const data = JSON.parse(ev.data)
      if (data.type === 'started') {
        setSessionId(data.session_id); setMonitoring(true)
        addAlert(`Monitoring started: "${sessionName || 'Meet session'}"`, 'ok')
      } else if (data.type === 'frame') {
        // Live preview (~24 fps). The sidecar draws the latest name + score
        // boxes on it; showing it is how the instructor sees what is matched.
        if (data.jpeg) setFrame(data.jpeg)
        // Older sidecars sent roster data with every frame.
        if (data.roster) setRoster(data.roster)
        if (data.unknowns) setUnknowns(data.unknowns.map((u: any, i: number) => ({ ...u, index: i })))
      } else if (data.type === 'analysis') {
        // Recognition results arrive separately, at the analysis rate.
        setRoster(data.roster || [])
        setUnknowns((data.unknowns || []).map((u: any, i: number) => ({ ...u, index: i })))
      } else if (data.type === 'alert') {
        addAlert(data.message, data.level)
      } else if (data.type === 'capture') {
        setCapture({ state: data.state, method: data.method })
      } else if (data.type === 'enrolled') {
        addAlert(`${data.name} enrolled from meeting.`, 'ok')
      } else if (data.type === 'verify_started') {
        setVerifyPrompt('Re-checking face against the enrolled photo…')
      } else if (data.type === 'verify_result') {
        setVerifyingId(null)
        setVerifyPrompt('')
        addAlert(data.message, data.ok ? 'ok' : 'error')
      } else if (data.type === 'challenge_started') {
        setCheck((c) => c && c.student.id === data.student_id
          ? { ...c, phase: 'running', instructions: data.instructions, chatText: data.chat_text,
              prompt: '', step: 1, steps: data.instructions.length + 1, result: null, error: '' }
          : c)
      } else if (data.type === 'challenge') {
        setCheck((c) => c && c.student.id === data.student_id && c.phase === 'running'
          ? { ...c, prompt: data.prompt, step: data.step, steps: data.steps,
              faceFound: data.face_found, smallFace: data.small_face,
              secondsLeft: data.seconds_left, result: data.result,
              phase: data.result ? 'done' : 'running' }
          : c)
      } else if (data.type === 'stopped') {
        setCheck(null)
        setMonitoring(false); setSessionId(null); setFrame(null); setCapture(null)
        addAlert('Monitoring stopped. Attendance recorded.', 'info')
        ws.close()
      } else if (data.type === 'error') {
        addAlert(`Error: ${data.message}`, 'error')
        // A check that could not start goes back to the choice screen.
        setCheck((c) => c && c.phase === 'starting' ? { ...c, phase: 'choose', error: data.message } : c)
      }
    }
    ws.onclose = () => { setMonitoring(false); setFrame(null); setCapture(null) }
    ws.onerror = () => addAlert('WebSocket connection error', 'error')
  }, [region, sessionName, missingAfter, classInfo.id])

  const sendWs = (payload: Record<string, unknown>) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload))
      return true
    }
    return false
  }

  const stopMonitoring = () => {
    if (!sendWs({ action: 'stop' })) {
      setMonitoring(false)
      setFrame(null)
    }
  }

  // Monitoring must not keep running after the user leaves the page.
  useEffect(() => {
    return () => {
      const ws = wsRef.current
      if (ws) {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: 'stop' }))
        }
        ws.close()
        wsRef.current = null
      }
    }
  }, [])

  const enrollUnknown = (u: UnknownFace) => setEnrollDialog({ unknown: u, studentNo: '', name: '' })

  const submitEnroll = () => {
    if (!enrollDialog) return
    const { unknown, studentNo, name } = enrollDialog
    if (!studentNo.trim() || !name.trim()) return
    // uid identifies the exact face that was clicked; the list position can
    // have shifted by the time this message arrives.
    sendWs({
      action: 'enroll_unknown', uid: unknown.uid, index: unknown.index,
      student_no: studentNo.trim(), name: name.trim(),
    })
    setEnrollDialog(null)
  }

  const onRosterClick = (student: RosterStudent) => {
    if (!monitoring) {
      addAlert('Start monitoring before verifying a student.', 'warn'); return
    }
    setCheck(newCheck(student))
  }

  // Keep the dialog's copy of the student current (state, suspect flag).
  useEffect(() => {
    setCheck((c) => {
      if (!c) return c
      const now = roster.find((r) => r.id === c.student.id)
      return now && (now.state !== c.student.state || now.suspect !== c.student.suspect)
        ? { ...c, student: now } : c
    })
  }, [roster])

  const startCheck = () => {
    if (!check) return
    if (!sendWs({ action: 'challenge', student_id: check.student.id })) {
      setCheck({ ...check, phase: 'choose', error: 'Not connected to the monitor.' }); return
    }
    setCheck({ ...newCheck(check.student), phase: 'starting' })
  }

  const quickCheck = () => {
    if (!check) return
    const student = check.student
    setCheck(null)
    if (!sendWs({ action: 'verify', student_id: student.id, timeout: 8.0 })) {
      addAlert('Not connected to the monitor.', 'error'); return
    }
    setVerifyingId(student.id)
    setVerifyPrompt(`Re-checking ${student.name}'s face…`)
  }

  const closeCheck = () => {
    if (check && (check.phase === 'running' || check.phase === 'starting')) {
      sendWs({ action: 'challenge_cancel' })
      addAlert(`Liveness check for ${check.student.name} cancelled.`, 'info')
    }
    setCheck(null)
  }

  return (
    <>
      {/* ── Hero Banner ──────────────────────────────────────────────── */}
      <section className="hero-banner">
        <div className="hero-top-row">
          <div className="hero-title-group">
            <h1 className="hero-title">Meeting Monitor</h1>
            <p className="hero-subtitle">Google Meet &amp; Zoom Real-Time Participant Face Tracking</p>
          </div>
          <div className="hero-action-cluster">
            {monitoring && (capture && PAUSED_CAPTURE[capture.state] ? (
              <div className="hero-status-pill" style={{ borderColor: 'var(--warn)', color: 'var(--warn)' }}
                   title="No picture of the window is available, so nobody is marked present or missing meanwhile">
                <span>Paused: {PAUSED_CAPTURE[capture.state]}</span>
              </div>
            ) : (
              <div className="hero-status-pill" style={{ borderColor: 'var(--present)', color: 'var(--present)' }}
                   title={capture?.method === 'wgc' ? 'Captured with Windows Graphics Capture: other windows can cover it' : undefined}>
                <span className="bubble-live-dot" />
                <span>Monitoring Active</span>
              </div>
            ))}
            {monitoring && capture?.state === 'minimized' && (
              <button className="btn-primary" onClick={() => sendWs({ action: 'restore_window' })}
                      title="Un-minimise the window without bringing it to the front">
                Keep monitoring
              </button>
            )}
            {/* Open / Close Bubble button */}
            {!bubbleOpen ? (
              <button className="btn-hero-launch" onClick={() => { OpenBubble(); setBubbleOpen(true) }}
                disabled={pickLoading}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/>
                </svg>
                Open Bubble
              </button>
            ) : (
              <button className="btn-hero-launch btn-hero-danger" onClick={() => { CloseBubble(); setBubbleOpen(false); if(monitoring) stopMonitoring() }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <circle cx="12" cy="12" r="9"/>
                  <line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
                </svg>
                Close Bubble
              </button>
            )}
          </div>
        </div>

        <div className="hero-tip-banner">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
            <circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/>
          </svg>
          <span>
            Pick what to watch with <strong>Screen Area</strong> or <strong>Select Window</strong>, then
            press <strong>Start Monitoring</strong>. <strong>Open Bubble</strong> puts the same controls in a
            floating circle that stays above Google Meet while you teach. Click a student in the roster
            to run a liveness check if their video looks suspicious.
          </span>
        </div>
      </section>

      {/* ── What is watched + Start ─────────────────────────────────── */}
      <section className={`launcher-card source-card ${region ? 'has-source' : 'no-source'} ${monitoring ? 'is-live' : ''}`}
               aria-label="What Presentia watches">
        <div className="source-preview" aria-hidden="true">
          {region && !region.hwnd && regionThumb
            ? <img src={regionThumb} alt="" />
            : (
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                {region?.hwnd
                  ? <><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M2 7h20"/><path d="M8 21h8"/><path d="M12 17v4"/></>
                  : <><path d="M3 8V5a2 2 0 0 1 2-2h3"/><path d="M16 3h3a2 2 0 0 1 2 2v3"/><path d="M21 16v3a2 2 0 0 1-2 2h-3"/><path d="M8 21H5a2 2 0 0 1-2-2v-3"/></>}
              </svg>
            )}
        </div>
        <div className="source-text">
          <span className="field-label" style={{ margin: 0 }}>
            {monitoring ? 'Monitoring' : region ? 'Ready to monitor' : 'Step 1 · Choose what to watch'}
          </span>
          {region ? (
            <>
              <div className="source-title">
                {region.hwnd ? (region.title || 'Selected window') : 'Screen area'}
              </div>
              <div className="source-sub">
                {region.hwnd
                  ? 'Followed when it moves, even behind other windows (not when minimised)'
                  : `${region.width} × ${region.height} px at ${region.left}, ${region.top}`}
              </div>
            </>
          ) : (
            <div className="source-sub">
              Drag over the meeting's video tiles, or pick the Meet / Zoom / Teams window.
            </div>
          )}
        </div>
        <div className="source-actions">
          {!monitoring && (
            <>
              <button className="btn-ghost" onClick={pickRegionFn} disabled={pickLoading}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
                  <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
                </svg>
                {pickLoading ? 'Capturing…' : region && !region.hwnd ? 'Change Area' : 'Screen Area'}
              </button>
              <button className="btn-ghost" onClick={openWinPickerFn}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/>
                </svg>
                {region?.hwnd ? 'Change Window' : 'Select Window'}
              </button>
              {region && (
                <button className="btn-icon source-clear" onClick={clearSource} title="Clear selection" aria-label="Clear selection">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                  </svg>
                </button>
              )}
            </>
          )}
          {monitoring ? (
            <button className="btn-primary source-start source-stop" onClick={stopMonitoring}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <rect x="6" y="6" width="12" height="12" rx="2"/>
              </svg>
              Stop Monitoring
            </button>
          ) : (
            <button className="btn-primary source-start" onClick={startMonitoringFn} disabled={!region}
                    title={region ? undefined : 'Choose a screen area or a window first'}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <polygon points="5 3 19 12 5 21 5 3"/>
              </svg>
              Start Monitoring
            </button>
          )}
        </div>
      </section>

      {/* ── 2-Card Dashboard ─────────────────────────────────────────── */}
      <section className="cards-grid">
        {/* Card 1: Session + Roster */}
        <div className="launcher-card">
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge emerald">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                  <circle cx="9" cy="7" r="4"/>
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                </svg>
              </div>
              <span className="card-title-text">Attendee Roster</span>
            </div>
            <span className="card-count-pill">{roster.length} students</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div>
              <label className="field-label">Meeting Name</label>
              <input className="input" placeholder="e.g. CS101 Lecture on Google Meet"
                value={sessionName} onChange={(e) => setSessionName(e.target.value)} disabled={monitoring}/>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <span className="field-label" style={{ margin: 0 }}>Alert after missing</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <input type="number" min={2} max={60} value={missingAfter}
                  onChange={(e) => setMissingAfter(Number(e.target.value))}
                  className="input" style={{ width: 60, textAlign: 'center', padding: '6px 8px' }} disabled={monitoring}/>
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>seconds</span>
              </div>
            </div>

            <div style={{ flex: 1, minHeight: 200, maxHeight: 300, overflowY: 'auto' }}>
              <RosterList students={roster} onStudentClick={onRosterClick} verifyingId={verifyingId}/>
              {verifyPrompt && (
                <div style={{ fontSize: 12, color: 'var(--warn)', marginTop: 8, fontWeight: 600 }}>{verifyPrompt}</div>
              )}
            </div>
          </div>
        </div>

        {/* Card 2: Activity & Unknown Faces */}
        <div className="launcher-card" style={{ flex: '1.5' }}>
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge orange">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
                  <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
                </svg>
              </div>
              <span className="card-title-text">Activity &amp; Detections</span>
            </div>
            <span className="card-count-pill">{alerts.length} events</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, flex: 1 }}>
            {/* Live view of the monitored area with recognition boxes */}
            {monitoring && (
              <VideoCanvas
                feed={feed}
                idle={!hasFrame}
                idleText="Waiting for the first captured frame…"
              />
            )}
            {unknowns.length > 0 && (
              <div style={{ padding: '10px 12px', borderRadius: 12, background: 'var(--card-row-bg)', border: '1px solid var(--border-subtle)' }}>
                <span className="field-label" style={{ marginBottom: 6 }}>Unknown Faces — Click to Enroll</span>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {unknowns.map((u, i) => (
                    <button key={i} onClick={() => enrollUnknown(u)}
                      style={{ width: 56, height: 56, borderRadius: 10, overflow: 'hidden',
                        border: '2px solid var(--accent)', padding: 0, cursor: 'pointer' }}
                      title="Click to register this face">
                      <img src={`data:image/jpeg;base64,${u.crop_jpeg}`} alt={`Unknown ${i+1}`}
                        style={{ width: '100%', height: '100%', objectFit: 'cover' }}/>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {!monitoring && !region && (
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', gap: 12, color: 'var(--muted)', padding: 24 }}>
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" opacity={0.35}>
                  <circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/>
                </svg>
                <p style={{ fontSize: 13, textAlign: 'center', maxWidth: 240 }}>
                  Choose <strong>Screen Area</strong> or <strong>Select Window</strong> above to tell Presentia
                  which part of the meeting to watch.
                </p>
              </div>
            )}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 180 }}>
              <span className="field-label">Live Activity Stream</span>
              <AlertList alerts={alerts} maxHeight={300}/>
            </div>
          </div>
        </div>
      </section>

      {/* ── Window Picker Modal ───────────────────────────────────────── */}
      {showWinPicker && (
        <div style={{ position: 'fixed', inset: 0, display: 'flex', alignItems: 'center',
          justifyContent: 'center', background: 'var(--overlay-bg)', zIndex: 200 }}>
          <div className="launcher-card" style={{ width: 420, maxWidth: 'calc(100vw - 32px)', padding: 20, gap: 12 }}>
            <div className="card-header" style={{ paddingBottom: 0 }}>
              <div className="card-header-left">
                <div className="card-icon-badge">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>
                  </svg>
                </div>
                <span className="card-title-text">Select a Window to Monitor</span>
              </div>
              <button className="btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={closeWinPicker}>✕</button>
            </div>
            <div style={{ maxHeight: 320, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
              {openWindows.length === 0
                ? <div style={{ padding: 20, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>No windows found</div>
                : openWindows.map((w, i) => (
                  <button key={i} onClick={() => selectWindow(w)}
                    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px',
                      borderRadius: 10, border: '1px solid var(--border-subtle)', background: 'var(--card-row-bg)',
                      cursor: 'pointer', textAlign: 'left', color: 'var(--ink)' }}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <rect x="2" y="3" width="20" height="14" rx="2"/>
                    </svg>
                    <span style={{ flex: 1, fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{w.title}</span>
                    <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace', flexShrink: 0 }}>{w.width}×{w.height}</span>
                  </button>
                ))
              }
            </div>
          </div>
        </div>
      )}

      {/* ── In-app Screen Picker Overlay ─────────────────────────────── */}
      {picking && screenshot && (
        <div
          ref={overlayRef}
          className="screen-picker-overlay"
          onMouseDown={onPickMouseDown}
          onMouseMove={onPickMouseMove}
          onMouseUp={onPickMouseUp}
          onContextMenu={(e) => { e.preventDefault(); cancelPick() }}
          tabIndex={-1}
        >
          <img src={`data:image/jpeg;base64,${screenshot.jpeg}`} alt="Screen" className="screen-picker-bg" draggable={false}/>
          <div className="screen-picker-dim" />
          <div className="screen-picker-hint">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
              <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
            </svg>
            Drag to select the area to monitor · <kbd>ESC</kbd> or right-click to cancel
            <button type="button" className="screen-picker-cancel"
              onMouseDown={(e) => e.stopPropagation()}
              onMouseUp={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); cancelPick() }}>
              Cancel
            </button>
          </div>
          {selRect && selRect.w > 2 && (
            <div className="screen-picker-sel" style={{ left: selRect.x, top: selRect.y, width: selRect.w, height: selRect.h }}>
              <div className="screen-picker-sel-label">
                {Math.round(selRect.w * (screenshot.width / (overlayRef.current?.clientWidth || 1)))} ×
                {Math.round(selRect.h * (screenshot.height / (overlayRef.current?.clientHeight || 1)))}
              </div>
            </div>
          )}
        </div>
      )}

      {check && (
        <VerifyDialog check={check} onStart={startCheck} onQuickCheck={quickCheck} onClose={closeCheck} />
      )}

      {/* ── Enroll Unknown Face Modal ─────────────────────────────────── */}
      {enrollDialog && (
        <div className="modal-scrim">
          <div className="launcher-card" style={{ width: 340, maxWidth: 'calc(100vw - 32px)', padding: 24, gap: 16 }}>
            <h3 style={{ fontSize: 16, color: 'var(--ink-heading)' }}>Enroll Face from Meeting</h3>
            <img src={`data:image/jpeg;base64,${enrollDialog.unknown.crop_jpeg}`} alt="Face"
              style={{ width: '100%', height: 140, objectFit: 'cover', borderRadius: 10, border: '1px solid var(--border-subtle)' }}/>
            <div>
              <label className="field-label">Student ID Number</label>
              <input className="input" placeholder="e.g. 2024-00123" value={enrollDialog.studentNo}
                onChange={(e) => setEnrollDialog((d) => d ? { ...d, studentNo: e.target.value } : d)}/>
            </div>
            <div>
              <label className="field-label">Full Name</label>
              <input className="input" placeholder="e.g. Juan Dela Cruz" value={enrollDialog.name}
                onChange={(e) => setEnrollDialog((d) => d ? { ...d, name: e.target.value } : d)}/>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn-primary" style={{ flex: 1 }} onClick={submitEnroll}>Enroll Student</button>
              <button className="btn-ghost" style={{ flex: 1 }} onClick={() => setEnrollDialog(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
