import React, { useState, useEffect, useRef, useCallback } from 'react'
import AlertList, { makeAlert } from '../components/AlertList'
import RosterList from '../components/RosterList'
import VideoCanvas from '../components/VideoCanvas'
import {
  GetOpenWindows,
  PrepareScreenPick, EnterPickerMode, ExitPickerMode,
  OpenBubble, CloseBubble,
} from '../../wailsjs/go/main/App'
import { EventsOn, EventsOff } from '../../wailsjs/runtime/runtime'

const API = 'http://127.0.0.1:7788'
const WS  = 'ws://127.0.0.1:7788'

interface RosterStudent {
  id: number
  name: string
  state: 'present' | 'missing' | 'waiting'
  verified?: boolean
}

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
}

interface ScreenShot {
  jpeg: string
  width: number
  height: number
  left: number
  top: number
}

export default function MeetPage() {
  const [sessionName, setSessionName]   = useState('')
  const [missingAfter, setMissingAfter] = useState(5)
  const [monitoring, setMonitoring]     = useState(false)
  const [sessionId, setSessionId]       = useState<number | null>(null)
  const [frame, setFrame]               = useState<string | null>(null)
  const [region, setRegion]             = useState<{ left: number; top: number; width: number; height: number } | null>(null)
  const [roster, setRoster]             = useState<RosterStudent[]>([])
  const [unknowns, setUnknowns]         = useState<UnknownFace[]>([])
  const [alerts, setAlerts]             = useState<AlertItem[]>([])
  const [enrollDialog, setEnrollDialog] = useState<EnrollDialog | null>(null)
  const [verifyingId, setVerifyingId]   = useState<number | null>(null)
  const [verifyPrompt, setVerifyPrompt] = useState('')
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

  // ── Native bubble event listeners ─────────────────────────────────
  // The native Win32 bubble (bubble_win.go) sends events via Go → Wails runtime.
  // Go's startup() re-emits them as "native:bubble:*" to the JS side.
  useEffect(() => {
    EventsOn('native:bubble:screen_area', () => { pickRegionFn() })
    EventsOn('native:bubble:win_picker',  () => { openWinPickerFn() })
    EventsOn('native:bubble:launch',      () => { startMonitoringFn() })
    EventsOn('native:bubble:stop',        () => { stopMonitoring() })
    EventsOn('native:bubble:quit',        () => {
      CloseBubble()
      setBubbleOpen(false)
    })
    // The bubble is a native Win32 window; if it cannot be created the button
    // must not stay stuck on "Close Bubble" with nothing on screen.
    EventsOn('native:bubble:failed',      () => {
      setBubbleOpen(false)
      addAlert('The floating bubble could not open — use the buttons above instead.', 'error')
    })
    return () => {
      EventsOff('native:bubble:screen_area')
      EventsOff('native:bubble:win_picker')
      EventsOff('native:bubble:launch')
      EventsOff('native:bubble:stop')
      EventsOff('native:bubble:quit')
      EventsOff('native:bubble:failed')
    }
  }, [region, sessionName, missingAfter, monitoring])

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

  const onPickMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
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
      if (selRect && selRect.w < 10) { setPicking(false); setScreenshot(null); try { await ExitPickerMode() } catch {} }
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
    dragStart.current = null
    setPicking(false)
    setScreenshot(null)
    setSelRect(null)
    try { await ExitPickerMode() } catch {}
    setRegion(region)
    addAlert(`Screen area selected: ${region.width}×${region.height}`, 'info')
  }

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

  const selectWindow = (w: WindowInfo) => {
    setRegion({ left: w.left, top: w.top, width: w.width, height: w.height })
    addAlert(`Window selected: "${w.title}"`, 'info')
    setShowWinPicker(false)
  }

  // ── Monitoring ────────────────────────────────────────────────────
  const startMonitoringFn = useCallback(() => {
    if (!region) { addAlert('Select a screen area or window first — use the bubble menu.', 'warn'); return }
    const ws = new WebSocket(`${WS}/ws/screen`)
    wsRef.current = ws
    ws.onopen = () => {
      const name = sessionName.trim() || `Meet ${new Date().toLocaleString()}`
      ws.send(JSON.stringify({ action: 'start', region, name, missing_after: missingAfter }))
    }
    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data)
      if (data.type === 'started') {
        setSessionId(data.session_id); setMonitoring(true)
        addAlert(`Monitoring started: "${sessionName || 'Meet session'}"`, 'ok')
      } else if (data.type === 'frame') {
        // The sidecar already draws name + score boxes on this frame; showing
        // it is the only way the instructor can see what is being matched.
        if (data.jpeg) setFrame(data.jpeg)
        setRoster(data.roster || [])
        setUnknowns((data.unknowns || []).map((u: any, i: number) => ({ ...u, index: i })))
      } else if (data.type === 'alert') {
        addAlert(data.message, data.level)
      } else if (data.type === 'enrolled') {
        addAlert(`${data.name} enrolled from meeting.`, 'ok')
      } else if (data.type === 'verify_started') {
        setVerifyPrompt('Re-checking face against the enrolled photo…')
      } else if (data.type === 'verify_result') {
        setVerifyingId(null)
        setVerifyPrompt('')
        addAlert(data.message, data.ok ? 'ok' : 'error')
      } else if (data.type === 'stopped') {
        setMonitoring(false); setSessionId(null); setFrame(null)
        addAlert('Monitoring stopped. Attendance recorded.', 'info')
        ws.close()
      } else if (data.type === 'error') {
        addAlert(`Error: ${data.message}`, 'error')
      }
    }
    ws.onclose = () => { setMonitoring(false); setFrame(null) }
    ws.onerror = () => addAlert('WebSocket connection error', 'error')
  }, [region, sessionName, missingAfter])

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
    if (student.state !== 'present') {
      addAlert(`${student.name} must be visible to verify.`, 'warn'); return
    }
    if (!sendWs({ action: 'verify', student_id: student.id, timeout: 8.0 })) {
      addAlert('Not connected to the monitor.', 'error'); return
    }
    setVerifyingId(student.id)
    setVerifyPrompt(`Verifying ${student.name}…`)
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
            {region && (
              <div className="hero-status-pill">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>
                </svg>
                <span>{region.width}×{region.height}</span>
              </div>
            )}
            {monitoring && (
              <div className="hero-status-pill" style={{ borderColor: 'var(--present)', color: 'var(--present)' }}>
                <span className="bubble-live-dot" />
                <span>Monitoring Active</span>
              </div>
            )}
            {/* In-app pickers — the bubble is a convenience, not the only way
                to choose what to monitor. */}
            <button className="btn-ghost" onClick={pickRegionFn} disabled={pickLoading || monitoring}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
                <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
              </svg>
              {pickLoading ? 'Capturing…' : 'Screen Area'}
            </button>
            <button className="btn-ghost" onClick={openWinPickerFn} disabled={monitoring}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/>
              </svg>
              Select Window
            </button>

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
            press <strong>Launch Monitor</strong>. <strong>Open Bubble</strong> puts the same controls in a
            floating circle that stays above Google Meet while you teach.
          </span>
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

            {region && !monitoring && (
              <button className="btn-primary" style={{ width: '100%', justifyContent: 'center' }}
                onClick={startMonitoringFn}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <polygon points="5 3 19 12 5 21 5 3"/>
                </svg>
                Launch Monitor
              </button>
            )}
            {monitoring && (
              <button className="btn-primary" style={{ width: '100%', justifyContent: 'center', background: 'var(--missing)' }}
                onClick={stopMonitoring}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <rect x="6" y="6" width="12" height="12" rx="2"/>
                </svg>
                Stop Monitoring
              </button>
            )}

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
                jpegBase64={frame}
                idle={!frame}
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
          <div className="launcher-card" style={{ width: 420, padding: 20, gap: 12 }}>
            <div className="card-header" style={{ paddingBottom: 0 }}>
              <div className="card-header-left">
                <div className="card-icon-badge">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>
                  </svg>
                </div>
                <span className="card-title-text">Select a Window to Monitor</span>
              </div>
              <button className="btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => setShowWinPicker(false)}>✕</button>
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
          onKeyDown={async (e) => { if (e.key === 'Escape') { setPicking(false); setScreenshot(null); setSelRect(null); try { await ExitPickerMode() } catch {} } }}
          tabIndex={0}
          autoFocus
        >
          <img src={`data:image/jpeg;base64,${screenshot.jpeg}`} alt="Screen" className="screen-picker-bg" draggable={false}/>
          <div className="screen-picker-dim" />
          <div className="screen-picker-hint">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
              <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
            </svg>
            Drag to select the area to monitor · <kbd>ESC</kbd> to cancel
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

      {/* ── Enroll Unknown Face Modal ─────────────────────────────────── */}
      {enrollDialog && (
        <div style={{ position: 'fixed', inset: 0, display: 'flex', alignItems: 'center',
          justifyContent: 'center', background: 'var(--overlay-bg)', zIndex: 200 }}>
          <div className="launcher-card" style={{ width: 340, padding: 24, gap: 16 }}>
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
