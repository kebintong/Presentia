import React, { useState, useEffect, useRef, useCallback } from 'react'
import VideoCanvas from '../components/VideoCanvas'

const API = 'http://127.0.0.1:7788'
const WS  = 'ws://127.0.0.1:7788'

interface Student {
  id: number
  student_no: string
  name: string
  created_at: string
}

interface EnrollStep {
  count: number
  total: number
  prompt: string
  done: boolean
  face_found: boolean
}

const POSE_LABELS = ['Straight', 'Left', 'Right', 'Up', 'Blink']

export default function RegisterPage() {
  const [studentNo, setStudentNo]       = useState('')
  const [name, setName]                 = useState('')
  const [students, setStudents]         = useState<Student[]>([])
  const [searchQuery, setSearchQuery]   = useState('')
  const [frame, setFrame]               = useState<string | null>(null)
  const [mode, setMode]                 = useState<string>('idle')
  const [progress, setProgress]         = useState<EnrollStep | null>(null)
  const [statusMsg, setStatusMsg]       = useState('Capture face samples via webcam or import photos.')
  const [canSave, setCanSave]           = useState(false)
  const [embeddingB64, setEmbeddingB64] = useState<string | null>(null)
  const [loading, setLoading]           = useState(false)
  // Track which steps just completed so we can re-trigger the pop animation
  const [completedSteps, setCompletedSteps] = useState<Set<number>>(new Set())
  // Field validation errors — shown as red border + shake
  const [fieldErrors, setFieldErrors]   = useState<{ studentNo?: boolean; name?: boolean }>({})
  const wsRef = useRef<WebSocket | null>(null)
  const prevCountRef = useRef<number>(-1)

  const loadStudents = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/students`)
      if (res.ok) {
        setStudents(await res.json())
      }
    } catch {
      // Backend starting
    }
  }, [])

  useEffect(() => {
    loadStudents()
  }, [loadStudents])

  const validateFields = (): boolean => {
    const errors: { studentNo?: boolean; name?: boolean } = {}
    if (!studentNo.trim()) errors.studentNo = true
    if (!name.trim()) errors.name = true
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      setStatusMsg('Please fill in Student ID Number and Full Name first.')
      // Auto-clear the red highlight after 2.5 s
      setTimeout(() => setFieldErrors({}), 2500)
      return false
    }
    setFieldErrors({})
    return true
  }

  const startWebcam = () => {
    if (mode === 'webcam') {
      stopCapture()
      return
    }
    if (!validateFields()) return
    stopCapture()
    setFrame(null)
    setProgress(null)
    setCanSave(false)
    setEmbeddingB64(null)
    setStatusMsg('Connecting to camera...')

    const ws = new WebSocket(`${WS}/ws/camera`)
    wsRef.current = ws

    ws.onopen = () => {
      ws.send(JSON.stringify({ action: 'start_enroll', directional: true }))
      setMode('webcam')
      setStatusMsg('Follow the on-screen prompts to capture 5 poses.')
    }

    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data)
      if (data.jpeg) setFrame(data.jpeg)
      if (data.type === 'enroll') {
        setProgress(data)
        // Detect a newly completed step and trigger the pop animation
        const newCount: number = data.count
        if (newCount > prevCountRef.current) {
          // Each completed step index is 0-based; count = how many done so far
          const justDoneIdx = newCount - 1
          if (justDoneIdx >= 0) {
            setCompletedSteps(prev => new Set(prev).add(justDoneIdx))
          }
          prevCountRef.current = newCount
        }
        if (data.done) {
          // Mark all steps done and capture the embedding sent by the sidecar
          setCompletedSteps(new Set([0, 1, 2, 3, 4]))
          if (data.embedding_b64) {
            console.log('[Presentia] Enrollment done — embedding received, length:', data.embedding_b64.length)
            setEmbeddingB64(data.embedding_b64)
          } else {
            console.warn('[Presentia] Enrollment done — but NO embedding_b64 in payload! Sidecar may be outdated.')
            setStatusMsg('Error: embedding not received. Please rebuild the sidecar.')
          }
          setStatusMsg(`All ${data.total} samples captured. Fill in details and press Save.`)
          setCanSave(!!data.embedding_b64)
          stopCapture()
        } else {
          setStatusMsg(data.prompt || `Pose ${data.count + 1}/${data.total}`)
        }
      }
    }

    ws.onerror = () => setStatusMsg('Camera error — check permissions.')
    ws.onclose = () => {
      if (mode === 'webcam') setMode('idle')
    }
  }

  const stopCapture = () => {
    const ws = wsRef.current
    if (ws) {
      // Sending on a socket that is still CONNECTING throws and would leave
      // the camera running on the sidecar side.
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'stop' }))
      }
      ws.close()
    }
    wsRef.current = null
    setMode('idle')
    setFrame(null)
    prevCountRef.current = -1
  }

  // Leaving the page must release the webcam.
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

  const importPhotos = async () => {
    if (!validateFields()) return
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/*'
    input.multiple = true
    input.onchange = async () => {
      const files = Array.from(input.files || []).slice(0, 5)
      if (!files.length) return
      setLoading(true)
      setStatusMsg('Extracting face data from photos...')
      try {
        // Preview only — the student record is written once, by Save.
        // The old flow created the student here and then POSTed to an
        // endpoint that did not exist, so saving always failed.
        const fd = new FormData()
        files.forEach((f) => fd.append('files', f))
        const res = await fetch(`${API}/api/enroll/photos/preview`, {
          method: 'POST',
          body: fd,
        })
        if (!res.ok) {
          const e = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
          setStatusMsg(`Error: ${e.detail}`)
          setLoading(false)
          return
        }
        const data = await res.json()
        if (!data.embedding_b64) {
          setStatusMsg('Error: no face data returned from the photos.')
          setLoading(false)
          return
        }
        setStatusMsg(`Face extracted from ${data.samples} photo(s). Check the details and press Save.`)
        setEmbeddingB64(data.embedding_b64)
        setCanSave(true)
        const reader = new FileReader()
        reader.onload = (e) => setFrame((e.target?.result as string).split(',')[1])
        reader.readAsDataURL(files[0])
      } catch (err: any) {
        setStatusMsg(`Error: ${err.message}`)
      } finally {
        setLoading(false)
      }
    }
    input.click()
  }

  const saveStudent = async () => {
    if (!studentNo.trim() || !name.trim()) {
      setStatusMsg('Student Number and Full Name are required.')
      return
    }
    if (!embeddingB64) {
      setStatusMsg('Capture face samples first.')
      return
    }
    setLoading(true)
    try {
      // One save path for both webcam capture and photo import.
      const res = await fetch(`${API}/api/students`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          student_no: studentNo.trim(),
          name: name.trim(),
          embedding_b64: embeddingB64,
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        const detail = String(err.detail || 'Failed to save')
        setStatusMsg(
          detail.includes('UNIQUE')
            ? `Error: student number ${studentNo.trim()} is already registered.`
            : `Error: ${detail}`
        )
        return
      }
      setStatusMsg(`Saved ${name.trim()} successfully!`)
      setStudentNo('')
      setName('')
      setEmbeddingB64(null)
      setCanSave(false)
      setProgress(null)
      setFrame(null)
      setCompletedSteps(new Set())
      prevCountRef.current = -1
      loadStudents()
    } catch (err: any) {
      setStatusMsg(`Error: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }

  const deleteStudent = async (id: number, studentName: string) => {
    if (!confirm(`Delete ${studentName}?`)) return
    try {
      const res = await fetch(`${API}/api/students/${id}`, { method: 'DELETE' })
      if (res.ok) {
        loadStudents()
        setStatusMsg(`Deleted ${studentName}`)
      }
    } catch (err: any) {
      setStatusMsg(`Error: ${err.message}`)
    }
  }

  // stepIdx = the index of the step currently being captured (0-based)
  // progress.count = how many samples have been collected so far
  const stepIdx = progress ? progress.count : -1

  const filteredStudents = students.filter(
    (s) =>
      s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.student_no.toLowerCase().includes(searchQuery.toLowerCase())
  )

  return (
    <>
      {/* ── LeviLauncher Hero Banner ─────────────────────────────────── */}
      <section className="hero-banner">
        <div className="hero-top-row">
          <div className="hero-title-group">
            <h1 className="hero-title">Presentia</h1>
            <p className="hero-subtitle">Biometric Facial Registration & Enrollment</p>
          </div>

          <div className="hero-action-cluster">
            <div className="hero-status-pill">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
              <span>{students.length} Enrolled</span>
            </div>

            {canSave ? (
              <button className="btn-hero-launch" onClick={saveStudent} disabled={loading}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                  <path d="M17 21v-8H7v8" />
                  <path d="M7 3v5h8" />
                </svg>
                {loading ? 'Saving...' : 'Save Student'}
              </button>
            ) : mode === 'webcam' ? (
              <button className="btn-hero-launch btn-hero-danger" onClick={stopCapture}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
                Stop Capture
              </button>
            ) : (
              <button className="btn-hero-launch" onClick={startWebcam}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                Launch Webcam
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
            Position face inside camera frame and follow 5 directional poses (Straight, Left, Right, Up, Blink) for high-accuracy recognition.
          </span>
        </div>
      </section>

      {/* ── 3-Card Modular Dashboard Grid ────────────────────────────── */}
      <section className="cards-grid">
        {/* Card 1: Student Information & Controls */}
        <div className="launcher-card">
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge purple">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                </svg>
              </div>
              <span className="card-title-text">Student Profile</span>
            </div>
            <span className="card-count-pill">Step 1</span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label className="field-label" style={{ color: fieldErrors.studentNo ? 'var(--missing)' : undefined }}>
                Student ID Number {fieldErrors.studentNo && <span style={{ fontSize: 10, fontWeight: 700 }}>← Required</span>}
              </label>
              <input
                className={`input ${fieldErrors.studentNo ? 'input-error' : ''}`}
                placeholder="e.g. 2024-00123"
                value={studentNo}
                onChange={(e) => { setStudentNo(e.target.value); if (fieldErrors.studentNo) setFieldErrors(p => ({ ...p, studentNo: false })) }}
              />
            </div>

            <div>
              <label className="field-label" style={{ color: fieldErrors.name ? 'var(--missing)' : undefined }}>
                Full Name {fieldErrors.name && <span style={{ fontSize: 10, fontWeight: 700 }}>← Required</span>}
              </label>
              <input
                className={`input ${fieldErrors.name ? 'input-error' : ''}`}
                placeholder="e.g. Juan Dela Cruz"
                value={name}
                onChange={(e) => { setName(e.target.value); if (fieldErrors.name) setFieldErrors(p => ({ ...p, name: false })) }}
              />
            </div>

            <div>
              <label className="field-label">Capture Mode</label>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <button
                  className="btn-primary"
                  onClick={startWebcam}
                  style={{ width: '100%', justifyContent: 'center' }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="3" />
                    <path d="M3 9a2 2 0 0 1 2-2h.93a2 2 0 0 0 1.664-.89l.812-1.22A2 2 0 0 1 10.07 4h3.86a2 2 0 0 1 1.664.89l.812 1.22A2 2 0 0 0 18.07 7H19a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9z" />
                  </svg>
                  {mode === 'webcam' ? 'Stop' : 'Webcam'}
                </button>

                <button
                  className="btn-ghost"
                  onClick={importPhotos}
                  disabled={loading}
                  style={{ width: '100%', justifyContent: 'center' }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" />
                    <circle cx="8.5" cy="8.5" r="1.5" />
                    <path d="M21 15l-5-5L5 21" />
                  </svg>
                  {loading ? 'Importing...' : 'Import'}
                </button>
              </div>
            </div>

            {/* Pose Progress Rings */}
            <div style={{ padding: '12px 14px', borderRadius: '12px', background: 'var(--card-row-bg)', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
                <span className="field-label" style={{ margin: 0 }}>Enrollment Steps</span>
                <span style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 600 }}>
                  {progress ? `${Math.min(progress.count, 5)} / 5` : '0 / 5'}
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
                {POSE_LABELS.map((lbl, i) => {
                  const isDone = completedSteps.has(i) || i < stepIdx
                  const isActive = !isDone && i === stepIdx
                  return (
                    <div
                      // key changes when step becomes done → forces DOM remount → re-triggers animation
                      key={`${i}-${isDone ? 'done' : isActive ? 'active' : 'idle'}`}
                      className={`step-ring ${isDone ? 'done' : isActive ? 'active' : ''}`}
                      title={lbl}
                      style={{ cursor: 'default' }}
                    >
                      {isDone ? (
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5">
                          <path d="M20 6L9 17l-5-5" />
                        </svg>
                      ) : (
                        i + 1
                      )}
                    </div>
                  )
                })}
              </div>
              {/* Pose label below active step */}
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6, marginTop: 6 }}>
                {POSE_LABELS.map((lbl, i) => (
                  <div key={i} style={{
                    flex: 1,
                    textAlign: 'center',
                    fontSize: 9,
                    fontWeight: 600,
                    letterSpacing: '0.03em',
                    textTransform: 'uppercase',
                    color: completedSteps.has(i) || i < stepIdx
                      ? 'var(--present)'
                      : i === stepIdx
                        ? 'var(--accent-vivid)'
                        : 'var(--muted-lo)',
                    transition: 'color 0.3s ease',
                  }}>{lbl}</div>
                ))}
              </div>
            </div>

            {/* Status Feedback */}
            <div style={{ padding: '10px 12px', borderRadius: '10px', background: 'var(--card-row-bg)', fontSize: 12, color: 'var(--muted)' }}>
              {statusMsg}
            </div>
          </div>
        </div>

        {/* Card 2: Camera Viewport */}
        <div className="launcher-card" style={{ flex: '1.2' }}>
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge emerald">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                  <circle cx="12" cy="13" r="4" />
                </svg>
              </div>
              <span className="card-title-text">Live Face Viewport</span>
            </div>
            <span className="card-count-pill">{mode === 'webcam' ? 'Streaming' : 'Idle'}</span>
          </div>

          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 280 }}>
            <VideoCanvas
              jpegBase64={frame}
              idle={!frame}
              idleText={mode === 'webcam' ? 'Connecting to camera...' : 'Start webcam capture or import photos'}
              className="flex-1"
            />
          </div>
        </div>

        {/* Card 3: Enrolled Roster */}
        <div className="launcher-card">
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge sapphire">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                </svg>
              </div>
              <span className="card-title-text">Enrolled Roster</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className="card-count-pill">{students.length}</span>
              <button className="btn-icon" onClick={loadStudents} title="Refresh Roster">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.93 3.25M3 3v6h6" />
                </svg>
              </button>
            </div>
          </div>

          {/* Search bar */}
          <input
            className="input"
            placeholder="Search students..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{ padding: '8px 12px', fontSize: 12.5 }}
          />

          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6, overflowY: 'auto', maxHeight: 260 }}>
            {filteredStudents.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '30px 0', color: 'var(--muted)', fontSize: 12.5 }}>
                {students.length === 0 ? 'No students enrolled yet' : 'No matching students'}
              </div>
            ) : (
              filteredStudents.map((s) => (
                <div key={s.id} className="card-row">
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--ink-heading)' }}>{s.name}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--muted)', fontFamily: 'monospace' }}>{s.student_no}</div>
                  </div>
                  <button
                    className="btn-ghost"
                    onClick={() => deleteStudent(s.id, s.name)}
                    style={{ padding: '4px 10px', fontSize: 11.5, color: 'var(--missing)' }}
                  >
                    Delete
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    </>
  )
}
