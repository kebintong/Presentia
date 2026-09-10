import React, { useState, useEffect, useRef } from 'react'
import VideoCanvas from '../components/VideoCanvas'
import AlertList, { makeAlert } from '../components/AlertList'
import RosterList from '../components/RosterList'

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
  crop_jpeg: string
  bbox: number[]
  index: number
}

interface EnrollDialog {
  unknown: UnknownFace
  studentNo: string
  name: string
}

export default function MeetPage() {
  const [sessionName, setSessionName]   = useState('')
  const [missingAfter, setMissingAfter] = useState(5)
  const [monitoring, setMonitoring]     = useState(false)
  const [sessionId, setSessionId]       = useState<number | null>(null)
  const [region, setRegion]             = useState<{ left: number; top: number; width: number; height: number } | null>(null)
  const [frame, setFrame]               = useState<string | null>(null)
  const [roster, setRoster]             = useState<RosterStudent[]>([])
  const [unknowns, setUnknowns]         = useState<UnknownFace[]>([])
  const [alerts, setAlerts]             = useState<AlertItem[]>([])
  const [enrollDialog, setEnrollDialog] = useState<EnrollDialog | null>(null)
  const [verifyingId, setVerifyingId]   = useState<number | null>(null)
  const [verifyPrompt, setVerifyPrompt] = useState('')
  const wsRef = useRef<WebSocket | null>(null)

  const addAlert = (message: string, level: AlertItem['level']) => {
    setAlerts((prev) => [makeAlert(message, level), ...prev].slice(0, 100))
  }

  // Screen region picker
  const pickRegion = () => {
    const overlay = window.open(
      '',
      '_blank',
      `width=${screen.width},height=${screen.height},top=0,left=0,menubar=no,toolbar=no,location=no,status=no`
    )
    if (!overlay) return
    overlay.document.write(`
      <html><head><style>
        body { margin:0; cursor:crosshair; background:rgba(0,0,0,0.3); }
        #sel { position:fixed; border:2px solid #00D29E; background:rgba(0,210,158,0.15); pointer-events:none; }
      </style></head>
      <body>
        <div id="sel" style="display:none"></div>
        <script>
          let sx,sy,dragging=false;
          const sel=document.getElementById('sel');
          document.onmousedown=e=>{sx=e.screenX;sy=e.screenY;dragging=true;sel.style.display='block';sel.style.left=sx+'px';sel.style.top=sy+'px';sel.style.width='0';sel.style.height='0';};
          document.onmousemove=e=>{if(!dragging)return;const x=Math.min(sx,e.screenX),y=Math.min(sy,e.screenY),w=Math.abs(e.screenX-sx),h=Math.abs(e.screenY-sy);sel.style.left=x+'px';sel.style.top=y+'px';sel.style.width=w+'px';sel.style.height=h+'px';};
          document.onmouseup=e=>{
            dragging=false;
            const x=Math.min(sx,e.screenX),y=Math.min(sy,e.screenY),w=Math.abs(e.screenX-sx),h=Math.abs(e.screenY-sy);
            if(w>10&&h>10){window.opener.postMessage({type:'region',region:{left:x,top:y,width:w,height:h}},'*');}
            window.close();
          };
        </script>
      </body></html>
    `)
  }

  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.data?.type === 'region') {
        setRegion(e.data.region)
        addAlert(`Screen area selected: ${e.data.region.width}x${e.data.region.height}`, 'info')
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [])

  // Monitoring handlers
  const startMonitoring = () => {
    if (!region) {
      addAlert('Select a screen area first.', 'warn')
      return
    }
    const ws = new WebSocket(`${WS}/ws/screen`)
    wsRef.current = ws

    ws.onopen = () => {
      const name = sessionName.trim() || `Meet ${new Date().toLocaleString()}`
      ws.send(
        JSON.stringify({
          action: 'start',
          region,
          name,
          missing_after: missingAfter,
        })
      )
    }

    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data)
      if (data.type === 'started') {
        setSessionId(data.session_id)
        setMonitoring(true)
        addAlert(`Monitoring started for "${sessionName || 'Meet session'}"`, 'ok')
      } else if (data.type === 'frame') {
        setFrame(data.jpeg)
        setRoster(data.roster || [])
        setUnknowns((data.unknowns || []).map((u: any, i: number) => ({ ...u, index: i })))
      } else if (data.type === 'alert') {
        addAlert(data.message, data.level)
      } else if (data.type === 'enrolled') {
        addAlert(`${data.name} enrolled from meeting.`, 'ok')
      } else if (data.type === 'stopped') {
        setMonitoring(false)
        setSessionId(null)
        setFrame(null)
        addAlert('Monitoring stopped. Attendance recorded.', 'info')
      } else if (data.type === 'error') {
        addAlert(`Error: ${data.message}`, 'error')
        stopMonitoring()
      }
    }

    ws.onerror = () => addAlert('WebSocket connection error', 'error')
  }

  const stopMonitoring = () => {
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ action: 'stop' }))
    }
  }

  const enrollUnknown = (u: UnknownFace) => {
    setEnrollDialog({ unknown: u, studentNo: '', name: '' })
  }

  const submitEnroll = () => {
    if (!enrollDialog) return
    const { unknown, studentNo, name } = enrollDialog
    if (!studentNo.trim() || !name.trim()) return
    wsRef.current?.send(
      JSON.stringify({
        action: 'enroll_unknown',
        index: unknown.index,
        student_no: studentNo.trim(),
        name: name.trim(),
      })
    )
    setEnrollDialog(null)
  }

  const onRosterClick = (student: RosterStudent) => {
    if (student.state !== 'present') {
      addAlert(`${student.name} must be visible to verify.`, 'warn')
      return
    }
    setVerifyingId(student.id)
    setVerifyPrompt(`Verifying ${student.name}...`)
  }

  return (
    <>
      {/* ── LeviLauncher Hero Banner ─────────────────────────────────── */}
      <section className="hero-banner">
        <div className="hero-top-row">
          <div className="hero-title-group">
            <h1 className="hero-title">Meeting Monitor</h1>
            <p className="hero-subtitle">Google Meet & Zoom Real-Time Participant Face Tracking</p>
          </div>

          <div className="hero-action-cluster">
            {/* Screen region selector pill */}
            <button className="hero-status-pill" onClick={pickRegion} disabled={monitoring} style={{ cursor: 'pointer' }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="2" y="3" width="20" height="14" rx="2" />
                <path d="M8 21h8" />
                <path d="M12 17v4" />
              </svg>
              <span>{region ? `${region.width}x${region.height}` : 'Select Screen Area'}</span>
            </button>

            {/* Launch / Stop Button */}
            {monitoring ? (
              <button className="btn-hero-launch btn-hero-danger" onClick={stopMonitoring}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
                Stop Monitoring
              </button>
            ) : (
              <button className="btn-hero-launch" onClick={startMonitoring} disabled={!region}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                Launch Monitor
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
            Select your Google Meet or Zoom window area. Presentia tracks attendees continuously and highlights unrecognized faces.
          </span>
        </div>
      </section>

      {/* ── 3-Card Modular Dashboard Grid ────────────────────────────── */}
      <section className="cards-grid">
        {/* Card 1: Attendee Roster & Session Config */}
        <div className="launcher-card">
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge emerald">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                  <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                </svg>
              </div>
              <span className="card-title-text">Attendee Roster</span>
            </div>
            <span className="card-count-pill">{roster.length} students</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div>
              <label className="field-label">Meeting Name</label>
              <input
                className="input"
                placeholder="e.g. CS101 Lecture on Google Meet"
                value={sessionName}
                onChange={(e) => setSessionName(e.target.value)}
                disabled={monitoring}
              />
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <span className="field-label" style={{ margin: 0 }}>Alert after missing</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <input
                  type="number"
                  min={2}
                  max={60}
                  value={missingAfter}
                  onChange={(e) => setMissingAfter(Number(e.target.value))}
                  className="input"
                  style={{ width: 60, textAlign: 'center', padding: '6px 8px' }}
                  disabled={monitoring}
                />
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>seconds</span>
              </div>
            </div>

            {/* Roster list */}
            <div style={{ flex: 1, minHeight: 200, maxHeight: 280, overflowY: 'auto' }}>
              <RosterList students={roster} onStudentClick={onRosterClick} verifyingId={verifyingId} />
              {verifyPrompt && (
                <div style={{ fontSize: 12, color: 'var(--warn)', marginTop: 8, fontWeight: 600 }}>
                  {verifyPrompt}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Card 2: Screen Feed Viewport */}
        <div className="launcher-card" style={{ flex: '1.3' }}>
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge sapphire">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="2" y="3" width="20" height="14" rx="2" />
                  <line x1="8" y1="21" x2="16" y2="21" />
                  <line x1="12" y1="17" x2="12" y2="21" />
                </svg>
              </div>
              <span className="card-title-text">Screen Monitor Stream</span>
            </div>
            <span className="card-count-pill">{monitoring ? 'Active Stream' : 'Standby'}</span>
          </div>

          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 300 }}>
            <VideoCanvas
              jpegBase64={frame}
              idle={!monitoring || !frame}
              idleText={monitoring ? 'Waiting for screen frames...' : 'Select screen area and click Launch Monitor'}
              className="flex-1"
            />
          </div>
        </div>

        {/* Card 3: Unknown Faces & Live Events */}
        <div className="launcher-card">
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge orange">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                  <path d="M13.73 21a2 2 0 0 1-3.46 0" />
                </svg>
              </div>
              <span className="card-title-text">Activity & Detections</span>
            </div>
            <span className="card-count-pill">{alerts.length} events</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, flex: 1 }}>
            {/* Unknown Faces Thumbnail Gallery */}
            {unknowns.length > 0 && (
              <div style={{ padding: '10px 12px', borderRadius: '12px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}>
                <span className="field-label" style={{ marginBottom: 6 }}>Unknown Faces (Click to Enroll)</span>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {unknowns.map((u, i) => (
                    <button
                      key={i}
                      onClick={() => enrollUnknown(u)}
                      style={{
                        width: 50,
                        height: 50,
                        borderRadius: 8,
                        overflow: 'hidden',
                        border: '2px solid rgba(0, 210, 158, 0.4)',
                        padding: 0,
                        cursor: 'pointer',
                        transition: 'transform 0.15s ease',
                      }}
                      title="Click to register this face"
                    >
                      <img
                        src={`data:image/jpeg;base64,${u.crop_jpeg}`}
                        alt={`Unknown ${i + 1}`}
                        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      />
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Real-time Alerts */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 180 }}>
              <span className="field-label">Live Activity Stream</span>
              <AlertList alerts={alerts} maxHeight={240} />
            </div>
          </div>
        </div>
      </section>

      {/* ── Enroll Unknown Face Modal Dialog ─────────────────────────── */}
      {enrollDialog && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'rgba(2, 6, 16, 0.75)',
            backdropFilter: 'blur(10px)',
            zIndex: 100,
          }}
        >
          <div className="launcher-card" style={{ width: 340, padding: 24, gap: 16 }}>
            <h3 style={{ fontSize: 16, color: 'var(--ink-heading)' }}>Enroll Face from Meeting</h3>
            <img
              src={`data:image/jpeg;base64,${enrollDialog.unknown.crop_jpeg}`}
              alt="Face"
              style={{ width: '100%', height: 140, objectFit: 'cover', borderRadius: 10, border: '1px solid rgba(255,255,255,0.12)' }}
            />
            <div>
              <label className="field-label">Student ID Number</label>
              <input
                className="input"
                placeholder="e.g. 2024-00123"
                value={enrollDialog.studentNo}
                onChange={(e) =>
                  setEnrollDialog((d) => (d ? { ...d, studentNo: e.target.value } : d))
                }
              />
            </div>
            <div>
              <label className="field-label">Full Name</label>
              <input
                className="input"
                placeholder="e.g. Juan Dela Cruz"
                value={enrollDialog.name}
                onChange={(e) =>
                  setEnrollDialog((d) => (d ? { ...d, name: e.target.value } : d))
                }
              />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn-primary" style={{ flex: 1 }} onClick={submitEnroll}>
                Enroll Student
              </button>
              <button className="btn-ghost" style={{ flex: 1 }} onClick={() => setEnrollDialog(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
