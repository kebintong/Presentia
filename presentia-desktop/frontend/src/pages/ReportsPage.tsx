import React, { useState, useEffect, useCallback } from 'react'

const API = 'http://127.0.0.1:7788'

interface Session {
  id: number
  name: string
  started_at: string
  ended_at: string | null
}

interface AttendanceRow {
  attendance_id: number
  student_no: string
  name: string
  time_in: string | null
  time_out: string | null
  status: string
  alert_count: number
}

interface EventRow {
  occurred_at: string
  event_type: string
  message: string
  student_name: string | null
}

const STATUSES = ['Present', 'Late', 'Absent']

export default function ReportsPage() {
  const [sessions, setSessions]       = useState<Session[]>([])
  const [selectedId, setSelectedId]   = useState<number | null>(null)
  const [rows, setRows]               = useState<AttendanceRow[]>([])
  const [events, setEvents]           = useState<EventRow[]>([])
  const [loading, setLoading]         = useState(false)
  const [searchFilter, setSearchFilter] = useState('')

  const loadSessions = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/sessions`)
      if (res.ok) {
        const data: Session[] = await res.json()
        setSessions(data)
        if (data.length > 0 && selectedId === null) {
          setSelectedId(data[0].id)
        }
      }
    } catch {
      // Backend starting
    }
  }, [selectedId])

  const loadSession = useCallback(async (id: number) => {
    setLoading(true)
    try {
      const [report, evs] = await Promise.all([
        fetch(`${API}/api/sessions/${id}/report`).then((r) => r.json()),
        fetch(`${API}/api/sessions/${id}/events`).then((r) => r.json()),
      ])
      setRows(report)
      setEvents(evs)
    } catch {
      // Session fetch error
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  useEffect(() => {
    if (selectedId !== null) loadSession(selectedId)
  }, [selectedId, loadSession])

  const changeStatus = async (attendanceId: number, status: string) => {
    try {
      await fetch(`${API}/api/attendance/${attendanceId}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      setRows((prev) =>
        prev.map((r) => (r.attendance_id === attendanceId ? { ...r, status } : r))
      )
    } catch {
      // Update error
    }
  }

  const exportCSV = async () => {
    if (!selectedId) return
    try {
      const res = await fetch(`${API}/api/sessions/${selectedId}/export-csv`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `attendance_session_${selectedId}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      // Download error
    }
  }

  const presentCount = rows.filter((r) => r.status === 'Present').length
  const lateCount    = rows.filter((r) => r.status === 'Late').length
  const absentCount  = rows.filter((r) => r.status === 'Absent').length
  const totalAlerts  = rows.reduce((acc, r) => acc + (r.alert_count || 0), 0)

  const filteredRows = rows.filter(
    (r) =>
      r.name.toLowerCase().includes(searchFilter.toLowerCase()) ||
      r.student_no.toLowerCase().includes(searchFilter.toLowerCase())
  )

  return (
    <>
      {/* ── LeviLauncher Hero Banner ─────────────────────────────────── */}
      <section className="hero-banner">
        <div className="hero-top-row">
          <div className="hero-title-group">
            <h1 className="hero-title">Attendance Reports</h1>
            <p className="hero-subtitle">Historical Records, Presence Analytics & CSV Export</p>
          </div>

          <div className="hero-action-cluster">
            {/* Session Selector Pill */}
            <div className="hero-status-pill">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
                <line x1="16" y1="2" x2="16" y2="6" />
                <line x1="8" y1="2" x2="8" y2="6" />
                <line x1="3" y1="10" x2="21" y2="10" />
              </svg>
              <select
                value={selectedId ?? ''}
                onChange={(e) => setSelectedId(Number(e.target.value))}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--ink)',
                  outline: 'none',
                  fontSize: 12.5,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                {sessions.map((s) => (
                  <option key={s.id} value={s.id} style={{ background: 'var(--select-opt-bg)', color: 'var(--ink)' }}>
                    #{s.id} — {s.name} ({s.started_at})
                  </option>
                ))}
              </select>
            </div>

            <button className="btn-icon" onClick={loadSessions} title="Refresh Sessions">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.93 3.25M3 3v6h6" />
              </svg>
            </button>

            {/* Big Launch Export Button */}
            <button
              className="btn-hero-launch"
              onClick={exportCSV}
              disabled={!selectedId || rows.length === 0}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              Export CSV
            </button>
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
            Select past sessions to review timestamps, verify attendance records, and download standard CSV reports for academic records.
          </span>
        </div>
      </section>

      {/* ── Metric Summary Chips ─────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
        <div className="launcher-card" style={{ padding: '14px 18px', gap: 6 }}>
          <span className="field-label" style={{ margin: 0 }}>Total Registered</span>
          <div style={{ fontSize: 24, fontWeight: 800, color: 'var(--ink-heading)', fontFamily: 'var(--display)' }}>
            {rows.length}
          </div>
        </div>

        <div className="launcher-card" style={{ padding: '14px 18px', gap: 6 }}>
          <span className="field-label" style={{ margin: 0, color: 'var(--present)' }}>Present</span>
          <div style={{ fontSize: 24, fontWeight: 800, color: 'var(--present)', fontFamily: 'var(--display)' }}>
            {presentCount}
          </div>
        </div>

        <div className="launcher-card" style={{ padding: '14px 18px', gap: 6 }}>
          <span className="field-label" style={{ margin: 0, color: 'var(--warn)' }}>Late</span>
          <div style={{ fontSize: 24, fontWeight: 800, color: 'var(--warn)', fontFamily: 'var(--display)' }}>
            {lateCount}
          </div>
        </div>

        <div className="launcher-card" style={{ padding: '14px 18px', gap: 6 }}>
          <span className="field-label" style={{ margin: 0, color: 'var(--missing)' }}>Absent / Alerts</span>
          <div style={{ fontSize: 24, fontWeight: 800, color: 'var(--missing)', fontFamily: 'var(--display)' }}>
            {absentCount} / {totalAlerts}
          </div>
        </div>
      </div>

      {/* ── 2 Modular Cards: Records Table & Event Log ───────────────── */}
      <section className="cards-grid" style={{ gridTemplateColumns: '2fr 1fr' }}>
        {/* Main Card: Attendance Records Table */}
        <div className="launcher-card" style={{ minHeight: 320 }}>
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge sapphire">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="16" y1="13" x2="8" y2="13" />
                  <line x1="16" y1="17" x2="8" y2="17" />
                </svg>
              </div>
              <span className="card-title-text">Attendance Roster Log</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                className="input"
                placeholder="Filter roster..."
                value={searchFilter}
                onChange={(e) => setSearchFilter(e.target.value)}
                style={{ width: 160, padding: '6px 10px', fontSize: 12 }}
              />
              <span className="card-count-pill">{filteredRows.length} students</span>
            </div>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', maxHeight: 360 }}>
            {loading ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, gap: 10, color: 'var(--muted)' }}>
                <span className="spinner" />
                <span>Loading report...</span>
              </div>
            ) : filteredRows.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '60px 0', color: 'var(--muted)' }}>
                No attendance records found for this session
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Student ID</th>
                    <th>Full Name</th>
                    <th>Time In</th>
                    <th>Time Out</th>
                    <th>Status</th>
                    <th>Alerts</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((row) => (
                    <tr key={row.attendance_id}>
                      <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{row.student_no}</td>
                      <td style={{ fontWeight: 600 }}>{row.name}</td>
                      <td style={{ fontSize: 12, color: 'var(--muted)', fontFamily: 'monospace' }}>
                        {row.time_in || '—'}
                      </td>
                      <td style={{ fontSize: 12, color: 'var(--muted)', fontFamily: 'monospace' }}>
                        {row.time_out || '—'}
                      </td>
                      <td>
                        <select
                          className="input"
                          value={row.status}
                          onChange={(e) => changeStatus(row.attendance_id, e.target.value)}
                          style={{ padding: '4px 8px', fontSize: 12, width: 100 }}
                        >
                          {STATUSES.map((s) => (
                            <option key={s} value={s}>
                              {s}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {row.alert_count > 0 ? (
                          <span className="badge badge-missing">{row.alert_count}</span>
                        ) : (
                          <span style={{ color: 'var(--muted)', fontSize: 12 }}>0</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Card 2: Session Event Log */}
        <div className="launcher-card">
          <div className="card-header">
            <div className="card-header-left">
              <div className="card-icon-badge orange">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
              </div>
              <span className="card-title-text">Event Log</span>
            </div>
            <span className="card-count-pill">{events.length} events</span>
          </div>

          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6, overflowY: 'auto', maxHeight: 360 }}>
            {events.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '60px 0', color: 'var(--muted)', fontSize: 12.5 }}>
                No events recorded for this session
              </div>
            ) : (
              events.map((ev, i) => (
                <div key={i} className="card-row" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                    <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'monospace' }}>
                      {ev.occurred_at}
                    </span>
                    <span className="badge badge-emerald" style={{ fontSize: 9.5 }}>
                      {ev.event_type}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--ink)' }}>
                    {ev.student_name ? <strong>{ev.student_name}: </strong> : null}
                    {ev.message}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    </>
  )
}
