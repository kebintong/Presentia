import React, { useState, useEffect, useRef, useCallback } from 'react'
import VideoCanvas from '../components/VideoCanvas'
import AlertList, { makeAlert } from '../components/AlertList'
import StatusBanner from '../components/StatusBanner'

const API = 'http://127.0.0.1:7788'
const WS  = 'ws://127.0.0.1:7788'

const LIVENESS_TIMEOUT = 60_000
const RECOGNIZE_ATTEMPTS = 12
const MATCH_THRESHOLD = 0.45

interface Student {
  id: number
  student_no: string
  name: string
}

interface AlertItem {
  id: number
  ts: string
  message: string
  level: 'ok' | 'warn' | 'error' | 'info'
}

type Phase = 'idle' | 'waiting' | 'liveness' | 'recognize' | 'monitoring'

export default function SessionPage() {
  const [sessionName, setSessionName]       = useState('')
  const [students, setStudents]             = useState<Student[]>([])
  const [selectedIdx, setSelectedIdx]       = useState(0)
  const [phase, setPhase]                   = useState<Phase>('idle')
  const [sessionId, setSessionId]           = useState<number | null>(null)
  const [frame, setFrame]                   = useState<string | null>(null)
  const [alerts, setAlerts]                 = useState<AlertItem[]>([])
  type BannerLevel = 'info' | 'ok' | 'warn' | 'error'
  const [banner, setBanner]                 = useState<{ text: string; level: BannerLevel }>({
    text: 'No active session. Click Start Session to begin.',
    level: 'info',
  })

  const wsRef = useRef<WebSocket | null>(null)
  const phaseStartRef = useRef<number>(0)
  const recCountRef = useRef(0)
  const activeStudentRef = useRef<Student | null>(null)
  const pendingStudentRef = useRef<Student | null>(null)

  const addAlert = useCallback((message: string, level: AlertItem['level']) => {
    setAlerts((prev) => [makeAlert(message, level), ...prev].slice(0, 100))
  }, [])

  const setBannerState = useCallback((text: string, level: BannerLevel) => {
    setBanner({ text, level })
  }, [])

  const loadStudents = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/students`)
      if (res.ok) {
        const data: Student[] = await res.json()
        setStudents(data)
      }
    } catch {
      // Server starting
    }
  }, [])

  useEffect(() => {
    loadStudents()
  }, [loadStudents])

  // Session lifecycle
  const startSession = async () => {
    await loadStudents()
    if (students.length === 0) {
      setBannerState('Register at least one student first.', 'error')
      return
    }
    const name = sessionName.trim() || `Session ${new Date().toLocaleString()}`
    try {
      const res = await fetch(`${API}/api/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      const { id } = await res.json()
      setSessionId(id)
      setPhase('waiting')
      setBannerState(`Session '${name}' started. Select a student and press Join & Verify.`, 'info')
      setAlerts([])
      openCameraWS()
    } catch (err: any) {
      setBannerState(`Error: ${err.message}`, 'error')
    }
  }

  const endSession = async () => {
    if (sessionId) {
      if (activeStudentRef.current) {
        await fetch(`${API}/api/sessions/${sessionId}/log-event`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            student_id: activeStudentRef.current.id,
            event_type: 'session_end',
            message: 'Session ended; time-out recorded.',
          }),
        })
      }
      await fetch(`${API}/api/sessions/${sessionId}/end`, { method: 'PUT' })
    }
    teardown()
    setBannerState('Session ended. Attendance times were recorded.', 'info')
  }

  const teardown = () => {
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ action: 'stop' }))
      wsRef.current.close()
      wsRef.current = null
    }
    setPhase('idle')
    setSessionId(null)
    setFrame(null)
    activeStudentRef.current = null
    pendingStudentRef.current = null
    recCountRef.current = 0
  }

  const openCameraWS = () => {
    const ws = new WebSocket(`${WS}/ws/camera`)
    wsRef.current = ws
    ws.onmessage = handleMessage
    ws.onerror = () => setBannerState('Camera error', 'error')
  }

  const startVerification = async () => {
    const student = students[selectedIdx]
    if (!student || !sessionId) return

    pendingStudentRef.current = student
    setPhase('liveness')
    phaseStartRef.current = Date.now()
    setBannerState(`Verifying ${student.name}: follow the on-screen prompts.`, 'info')

    wsRef.current?.send(
      JSON.stringify({
        action: 'start_liveness',
        directional: true,
        student_id: student.id,
      })
    )
  }

  const handleMessage = useCallback(
    (ev: MessageEvent) => {
      const data = JSON.parse(ev.data)
      if (data.jpeg) setFrame(data.jpeg)

      if (data.type === 'liveness') {
        if (Date.now() - phaseStartRef.current > LIVENESS_TIMEOUT) {
          setPhase('waiting')
          wsRef.current?.send(JSON.stringify({ action: 'stop' }))
          setBannerState('Liveness check timed out. Press Join & Verify to retry.', 'error')
          return
        }
        if (data.passed) {
          setPhase('recognize')
          recCountRef.current = 0
          setBannerState('Liveness passed. Verifying facial identity...', 'info')
          wsRef.current?.send(
            JSON.stringify({
              action: 'start_recognize',
              student_id: pendingStudentRef.current?.id,
            })
          )
        } else {
          setBannerState(`Liveness check: ${data.prompt}`, 'info')
        }
      }

      if (data.type === 'recognize') {
        recCountRef.current += 1
        if (data.found && data.score >= MATCH_THRESHOLD) {
          const student = pendingStudentRef.current!
          activeStudentRef.current = student
          pendingStudentRef.current = null
          const sid = sessionId!

          // Record time-in
          fetch(`${API}/api/attendance/time-in`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sid, student_id: student.id }),
          })
          fetch(`${API}/api/sessions/${sid}/log-event`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              student_id: student.id,
              event_type: 'verified',
              message: `Identity verified (score ${data.score.toFixed(2)}); time-in recorded.`,
            }),
          })
          addAlert(`${student.name} verified: time-in recorded.`, 'ok')
          setBannerState(`${student.name} is verified and being monitored.`, 'ok')
          setPhase('monitoring')
          wsRef.current?.send(
            JSON.stringify({
              action: 'start_monitor',
              student_id: student.id,
            })
          )
          return
        }
        if (recCountRef.current >= RECOGNIZE_ATTEMPTS) {
          const student = pendingStudentRef.current
          setPhase('waiting')
          wsRef.current?.send(JSON.stringify({ action: 'stop' }))
          addAlert(`Verification failed for ${student?.name}.`, 'error')
          setBannerState(`Face does not match ${student?.name}. Press Join & Verify to retry.`, 'error')
        }
      }

      if (data.type === 'presence_alert') {
        const level =
          data.event_type === 'back_in_frame'
            ? 'ok'
            : data.event_type === 'camera_off'
            ? 'error'
            : 'warn'
        addAlert(data.message, level as AlertItem['level'])
        const student = activeStudentRef.current
        if (data.event_type === 'back_in_frame') {
          setBannerState(`${student?.name} is present and being monitored.`, 'ok')
        } else if (data.event_type === 'out_of_frame') {
          setBannerState(`ALERT: ${student?.name} is out of frame!`, 'warn')
        } else if (data.event_type === 'camera_off') {
          setBannerState(`ALERT: ${student?.name}'s camera is off!`, 'error')
        } else if (data.event_type === 'identity_mismatch') {
          setBannerState('ALERT: a different person may be on camera!', 'error')
        }

        // Log presence event
        if (sessionId && student) {
          fetch(`${API}/api/sessions/${sessionId}/log-event`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              student_id: student.id,
              event_type: data.event_type,
              message: data.message,
            }),
          })
        }
      }
    },
    [sessionId, addAlert, setBannerState]
  )

  useEffect(() => {
    if (wsRef.current) wsRef.current.onmessage = handleMessage
  }, [handleMessage])

  const bannerSpinner = phase === 'liveness' || phase === 'recognize'

  return (
    <>
      {/* ── LeviLauncher Hero Banner ─────────────────────────────────── */}
      <section className="hero-banner">
        <div className="hero-top-row">
          <div className="hero-title-group">
            <h1 className="hero-title">Class Session</h1>
            <p className="hero-subtitle">Live Attendance Verification & Continuous Presence Monitoring</p>
          </div>

          <div className="hero-action-cluster">
            <div className="hero-status-pill">
              <span className="engine-dot" style={{ background: phase === 'monitoring' ? 'var(--present)' : 'var(--muted)' }} />
              <span>
                {phase === 'idle'
                  ? 'Session Idle'
                  : phase === 'waiting'
                  ? 'Waiting for Check-in'
                  : phase === 'monitoring'
                  ? 'Active Monitoring'
                  : phase.toUpperCase()}
              </span>
            </div>

            {phase === 'idle' ? (
              <button className="btn-hero-launch" onClick={startSession}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                Launch Session
              </button>
            ) : (
              <button className="btn-hero-launch btn-hero-danger" onClick={endSession}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
                End Session
              </button>
            )}
          </div>
        </div>

        {/* Tip / Notice Bar */}
        <div className="hero-tip-banner">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <path d="M9 18h6" />
            <path d="M10 22h4" />
            <path d="M12 2a7 7 0 0 0-7 7c0 2.38 1.19 4.47 3 5.74V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.26c1.81-1.27 3-3.36 3-5.74a7 7 0 0 0-7-7z" />
          </svg>
          <span>
            Select an enrolled student to verify facial biometric identity. Liveness confirmation is required before continuous presence tracking activates.
          </span>
        </div>
      </section>

      {/* ── 3-Card Modular Dashboard Grid ────────────────────────────── */}
      <section className="cards-grid">
        {/* Card 1: Check-in Controls & Student Selector */}
        <div className="launcher-card">
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge emerald">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                  <circle cx="12" cy="7" r="4" />
                </svg>
              </div>
              <span className="card-title-text">Attendance Check-in</span>
            </div>
            <span className="card-count-pill">{students.length} students</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label className="field-label">Session Name</label>
              <input
                className="input"
                placeholder="e.g. CS101 Afternoon Lecture"
                value={sessionName}
                onChange={(e) => setSessionName(e.target.value)}
                disabled={phase !== 'idle'}
              />
            </div>

            <div>
              <label className="field-label">Select Student to Verify</label>
              <select
                className="input"
                value={selectedIdx}
                onChange={(e) => setSelectedIdx(Number(e.target.value))}
                disabled={phase !== 'waiting'}
              >
                {students.map((s, i) => (
                  <option key={s.id} value={i}>
                    {s.student_no} — {s.name}
                  </option>
                ))}
              </select>
            </div>

            <button
              className="btn-primary"
              disabled={phase !== 'waiting' || students.length === 0}
              onClick={startVerification}
              style={{ width: '100%', justifyContent: 'center', padding: '11px 18px' }}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              Join & Verify Face
            </button>

            {/* Step info summary */}
            <div style={{ padding: '12px 14px', borderRadius: '12px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
              <span className="field-label" style={{ marginBottom: 6 }}>Check-in Workflow</span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: 'var(--muted)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ color: phase !== 'idle' ? 'var(--present)' : 'var(--muted)' }}>✓</span>
                  <span>1. Start Class Session</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ color: phase === 'liveness' || phase === 'recognize' || phase === 'monitoring' ? 'var(--present)' : 'var(--muted)' }}>✓</span>
                  <span>2. Complete Liveness Pose</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ color: phase === 'monitoring' ? 'var(--present)' : 'var(--muted)' }}>✓</span>
                  <span>3. Continuous Face Monitoring</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Card 2: Verification Viewport */}
        <div className="launcher-card" style={{ flex: '1.3' }}>
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge sapphire">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                  <circle cx="12" cy="13" r="4" />
                </svg>
              </div>
              <span className="card-title-text">Verification Stream</span>
            </div>
            <span className="card-count-pill">
              {phase === 'monitoring' ? 'Monitoring' : phase === 'liveness' ? 'Liveness Check' : 'Camera Feed'}
            </span>
          </div>

          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 300, gap: 10 }}>
            <VideoCanvas
              jpegBase64={frame}
              idle={phase === 'idle' || !frame}
              idleText={phase === 'idle' ? 'Click Launch Session to open camera' : 'Waiting for video stream...'}
              className="flex-1"
            />
            <StatusBanner text={banner.text} level={banner.level} spinner={bannerSpinner} />
          </div>
        </div>

        {/* Card 3: Real-Time Presence Activity */}
        <div className="launcher-card">
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge orange">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
              </div>
              <span className="card-title-text">Presence Activity</span>
            </div>
            <span className="card-count-pill">{alerts.length} events</span>
          </div>

          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 220 }}>
            <AlertList alerts={alerts} maxHeight={280} />
          </div>
        </div>
      </section>
    </>
  )
}
