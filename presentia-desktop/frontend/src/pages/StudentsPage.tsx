import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Modal from '../components/Modal'
import ReuseStudentsModal from '../components/ReuseStudentsModal'
import { SaveExcelToFile } from '../../wailsjs/go/main/App'
import { ClassInfo, formatDateTime, formatDay, formatTime, shortDate } from '../classes'

const API = 'http://127.0.0.1:7788'

/** Below this share of sessions attended, a student is flagged. Many
 *  schools drop students who miss more than 20% of classes. */
const LOW_ATTENDANCE = 0.8

interface StudentSummary {
  id: number
  student_no: string
  name: string
  joined_at: string
  registered_at: string
  present: number
  late: number
  absent: number
  /** Sessions that count for this student (held after they joined). */
  sessions: number
  /** (present + late) / sessions, or null when nothing has counted yet. */
  rate: number | null
  last_seen: string | null
  alerts: number
}

interface HistoryRow {
  session_id: number
  name: string
  started_at: string
  ended_at: string | null
  time_in: string | null
  time_out: string | null
  counted: boolean
  status: 'Present' | 'Late' | 'Absent' | null
}

type SortKey = 'student_no' | 'name' | 'joined_at' | 'present' | 'late' | 'absent' | 'rate' | 'last_seen'
type Filter = 'all' | 'low'

const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: 'student_no', label: 'Student ID' },
  { key: 'name', label: 'Name' },
  { key: 'joined_at', label: 'Joined' },
  { key: 'present', label: 'Present', numeric: true },
  { key: 'late', label: 'Late', numeric: true },
  { key: 'absent', label: 'Absent', numeric: true },
  { key: 'rate', label: 'Attendance', numeric: true },
  { key: 'last_seen', label: 'Last seen' },
]

const isLow = (s: StudentSummary) => s.rate !== null && s.rate < LOW_ATTENDANCE

function pct(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`
}

function capitalize(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s
}

async function errorText(res: Response): Promise<string> {
  const body = await res.json().catch(() => null)
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return String(detail.message)
  return `Request failed (HTTP ${res.status})`
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf)
  let binary = ''
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK))
  }
  return btoa(binary)
}

/** "attachment; filename*=UTF-8''IT%20101...xlsx" → "IT 101....xlsx" */
function filenameFrom(header: string | null, fallback: string): string {
  const m = header?.match(/filename\*=UTF-8''([^;]+)/i)
  if (m) {
    try { return decodeURIComponent(m[1]) } catch { /* fall through */ }
  }
  return fallback
}

/**
 * Students page: the class roster with each student's attendance totals.
 * Sort, filter low attendance, see one student's session history, fix a
 * name or number, remove from the class, and export everything to Excel.
 */
export default function StudentsPage({ classInfo }: { classInfo: ClassInfo }) {
  const [students, setStudents] = useState<StudentSummary[] | null>(null)
  const [sessionsHeld, setSessionsHeld] = useState(classInfo.session_count)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [sortKey, setSortKey] = useState<SortKey>('name')
  const [sortAsc, setSortAsc] = useState(true)
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null)
  const [exporting, setExporting] = useState(false)

  const [menuFor, setMenuFor] = useState<number | null>(null)
  const [historyFor, setHistoryFor] = useState<StudentSummary | null>(null)
  const [history, setHistory] = useState<HistoryRow[] | null>(null)
  const [editing, setEditing] = useState<StudentSummary | null>(null)
  const [editForm, setEditForm] = useState({ student_no: '', name: '' })
  const [editError, setEditError] = useState('')
  const [editBusy, setEditBusy] = useState(false)
  const [reuseOpen, setReuseOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      const [res, cls] = await Promise.all([
        fetch(`${API}/api/classes/${classInfo.id}/students/summary`),
        fetch(`${API}/api/classes/${classInfo.id}`),
      ])
      if (res.ok) setStudents(await res.json())
      if (cls.ok) setSessionsHeld((await cls.json()).session_count)
    } catch {
      // Sidecar starting; the refresh button retries.
    }
  }, [classInfo.id])

  useEffect(() => { load() }, [load])

  // Close the row menu on an outside click.
  const menuRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (menuFor === null) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuFor(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuFor])

  const all = students ?? []
  const withRate = all.filter((s) => s.rate !== null)
  const avgRate = withRate.length ? withRate.reduce((t, s) => t + (s.rate as number), 0) / withRate.length : null
  const lowCount = all.filter(isLow).length

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = all.filter(
      (s) =>
        (filter === 'all' || isLow(s)) &&
        (!q || s.name.toLowerCase().includes(q) || s.student_no.toLowerCase().includes(q))
    )
    const dir = sortAsc ? 1 : -1
    return [...list].sort((a, b) => {
      const va = a[sortKey]
      const vb = b[sortKey]
      // Empty values (no rate yet, never seen) always go last.
      if (va === null || va === undefined) return vb === null || vb === undefined ? 0 : 1
      if (vb === null || vb === undefined) return -1
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
      return String(va).localeCompare(String(vb), undefined, { numeric: true }) * dir
    })
  }, [all, query, filter, sortKey, sortAsc])

  const sortBy = (key: SortKey, numeric?: boolean) => {
    if (key === sortKey) {
      setSortAsc((v) => !v)
    } else {
      setSortKey(key)
      // Numbers read best biggest-first; text A→Z.
      setSortAsc(!numeric)
    }
  }

  const openHistory = async (s: StudentSummary) => {
    setMenuFor(null)
    setHistoryFor(s)
    setHistory(null)
    try {
      const res = await fetch(`${API}/api/classes/${classInfo.id}/students/${s.id}/history`)
      setHistory(res.ok ? await res.json() : [])
    } catch {
      setHistory([])
    }
  }

  const openEdit = (s: StudentSummary) => {
    setMenuFor(null)
    setEditForm({ student_no: s.student_no, name: s.name })
    setEditError('')
    setEditing(s)
  }

  const saveEdit = async () => {
    if (!editing) return
    if (!editForm.student_no.trim() || !editForm.name.trim()) {
      setEditError('Student ID and name are both required.')
      return
    }
    setEditBusy(true)
    try {
      const res = await fetch(`${API}/api/students/${editing.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ student_no: editForm.student_no.trim(), name: editForm.name.trim() }),
      })
      if (!res.ok) {
        setEditError(await errorText(res))
        return
      }
      setEditing(null)
      setNotice({ text: `Updated ${editForm.name.trim()}.` })
      load()
    } catch (err: any) {
      setEditError(err?.message || 'Could not reach the Presentia engine.')
    } finally {
      setEditBusy(false)
    }
  }

  const remove = async (s: StudentSummary) => {
    setMenuFor(null)
    if (!confirm(
      `Remove ${s.name} from ${classInfo.name}?\n\n` +
      'If this is their only class, their face data and attendance records are deleted. ' +
      'Otherwise they stay in their other classes.'
    )) return
    try {
      const res = await fetch(`${API}/api/classes/${classInfo.id}/students/${s.id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(await errorText(res))
      const { deleted } = await res.json()
      setNotice({
        text: deleted
          ? `Removed ${s.name} and deleted their face data.`
          : `Removed ${s.name} from this class (they stay in their other classes).`,
      })
      load()
    } catch (err: any) {
      setNotice({ text: `Could not remove ${s.name}: ${err.message}`, error: true })
    }
  }

  const exportExcel = async () => {
    setExporting(true)
    setNotice(null)
    try {
      const res = await fetch(`${API}/api/classes/${classInfo.id}/export.xlsx`)
      if (!res.ok) throw new Error(await errorText(res))
      const name = filenameFrom(res.headers.get('Content-Disposition'), `${classInfo.name} attendance.xlsx`)
      const buf = await res.arrayBuffer()

      // Native save dialog inside the desktop app; a normal download when
      // the UI is opened in a plain browser for testing.
      if ((window as any)['go']?.['main']?.['App']?.['SaveExcelToFile']) {
        const path = await SaveExcelToFile(name, arrayBufferToBase64(buf))
        setNotice(path ? { text: `Saved to ${path}` } : { text: 'Export cancelled.' })
        return
      }
      const url = URL.createObjectURL(
        new Blob([buf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
      )
      const a = document.createElement('a')
      a.href = url
      a.download = name
      a.click()
      URL.revokeObjectURL(url)
      setNotice({ text: `Downloaded ${name}` })
    } catch (err: any) {
      setNotice({ text: `Export failed: ${err.message}`, error: true })
    } finally {
      setExporting(false)
    }
  }

  const statCard = (label: string, value: React.ReactNode, color?: string, sub?: string) => (
    <div className="launcher-card" style={{ padding: '14px 18px', gap: 6 }}>
      <span className="field-label" style={{ margin: 0, color }}>{label}</span>
      <div style={{ fontSize: 24, fontWeight: 800, color: color || 'var(--ink-heading)', fontFamily: 'var(--display)' }}>
        {value}
      </div>
      {sub && <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>{sub}</span>}
    </div>
  )

  return (
    <>
      <section className="hero-banner">
        <div className="hero-top-row">
          <div className="hero-title-group">
            <h1 className="hero-title">Students</h1>
            <p className="hero-subtitle">
              Attendance for each student in <strong style={{ color: 'var(--ink)' }}>{classInfo.name}</strong>
            </p>
          </div>
          <div className="hero-action-cluster">
            <button className="btn-ghost" onClick={() => setReuseOpen(true)} style={{ borderRadius: 'var(--radius-pill)' }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M19 8v6M22 11h-6" />
              </svg>
              Add from other classes
            </button>
            <button className="btn-hero-launch" onClick={exportExcel} disabled={exporting || all.length === 0}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              {exporting ? 'Exporting…' : 'Export to Excel'}
            </button>
          </div>
        </div>

        {notice && (
          <div
            className="hero-tip-banner"
            role="status"
            style={notice.error ? { background: 'var(--missing-dim)', borderColor: 'transparent', color: 'var(--missing)' } : undefined}
          >
            <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{notice.text}</span>
            <button className="btn-ghost" style={{ padding: '3px 10px', fontSize: 11.5 }} onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        )}
      </section>

      <div className="stats-grid">
        {statCard('Students', all.length)}
        {statCard('Sessions held', sessionsHeld)}
        {statCard('Average attendance', pct(avgRate), avgRate !== null && avgRate < LOW_ATTENDANCE ? 'var(--missing)' : 'var(--present)')}
        {statCard(
          `Below ${Math.round(LOW_ATTENDANCE * 100)}%`,
          lowCount,
          lowCount > 0 ? 'var(--missing)' : undefined,
        )}
      </div>

      <section className="launcher-card" style={{ minHeight: 320 }}>
        <div className="card-header" style={{ flexWrap: 'wrap', gap: 10 }}>
          <div className="card-header-left">
            <div className="card-icon-badge sapphire">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
            </div>
            <span className="card-title-text">Class Roster</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div className="seg-toggle" role="group" aria-label="Filter students">
              <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>All</button>
              <button className={filter === 'low' ? 'active' : ''} onClick={() => setFilter('low')}>
                Below {Math.round(LOW_ATTENDANCE * 100)}%{lowCount > 0 ? ` (${lowCount})` : ''}
              </button>
            </div>
            <input
              className="input"
              placeholder="Search name or ID…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ width: 180, padding: '6px 10px', fontSize: 12 }}
            />
            <button className="btn-icon" onClick={load} title="Refresh" aria-label="Refresh">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.93 3.25M3 3v6h6" />
              </svg>
            </button>
          </div>
        </div>

        <div className="students-table-wrap">
          {students === null ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, gap: 10, color: 'var(--muted)' }}>
              <span className="spinner" />
              <span>Loading students…</span>
            </div>
          ) : shown.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '56px 0', color: 'var(--muted)', fontSize: 13 }}>
              {all.length === 0
                ? 'No students in this class yet. Register them on the Register page, or add them from your other classes.'
                : filter === 'low' && !query
                  ? `Nobody is below ${Math.round(LOW_ATTENDANCE * 100)}% attendance.`
                  : 'No matching students.'}
            </div>
          ) : (
            <table className="data-table students-table">
              <thead>
                <tr>
                  {COLUMNS.map((c) => (
                    <th
                      key={c.key}
                      className={c.numeric ? 'num' : undefined}
                      aria-sort={sortKey === c.key ? (sortAsc ? 'ascending' : 'descending') : 'none'}
                    >
                      <button className="th-sort" onClick={() => sortBy(c.key, c.numeric)}>
                        {c.label}
                        <span className="th-sort-arrow" aria-hidden="true">
                          {sortKey === c.key ? (sortAsc ? '▲' : '▼') : ''}
                        </span>
                      </button>
                    </th>
                  ))}
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {shown.map((s, i) => (
                  <tr key={s.id} className="students-row" onClick={() => openHistory(s)}>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{s.student_no}</td>
                    <td style={{ fontWeight: 600, color: 'var(--ink-heading)' }}>{s.name}</td>
                    <td style={{ fontSize: 12, color: 'var(--muted)' }} title={formatDateTime(s.joined_at)}>
                      {formatDay(s.joined_at)}
                    </td>
                    <td className="num" style={{ color: 'var(--present)', fontWeight: 700 }}>{s.present}</td>
                    <td className="num" style={{ color: 'var(--warn)', fontWeight: 700 }}>{s.late}</td>
                    <td className="num" style={{ color: 'var(--missing)', fontWeight: 700 }}>{s.absent}</td>
                    <td className="num">
                      <div className="rate-cell" title={`${s.present + s.late} of ${s.sessions} sessions`}>
                        <div className="rate-track" aria-hidden="true">
                          <div
                            className={`rate-fill ${isLow(s) ? 'low' : ''}`}
                            style={{ width: `${Math.round((s.rate ?? 0) * 100)}%` }}
                          />
                        </div>
                        <span className={`rate-pct ${isLow(s) ? 'rate-low' : ''}`}>{pct(s.rate)}</span>
                      </div>
                    </td>
                    <td style={{ fontSize: 12, color: 'var(--muted)' }} title={formatDateTime(s.last_seen)}>
                      {s.last_seen ? capitalize(shortDate(s.last_seen)) : 'Never'}
                    </td>
                    <td onClick={(e) => e.stopPropagation()} style={{ width: 44 }}>
                      <div className="row-menu-anchor" ref={menuFor === s.id ? menuRef : undefined}>
                        <button
                          className="class-menu-btn"
                          aria-label={`Options for ${s.name}`}
                          aria-haspopup="menu"
                          aria-expanded={menuFor === s.id}
                          onClick={() => setMenuFor(menuFor === s.id ? null : s.id)}
                        >
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                            <circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" />
                          </svg>
                        </button>
                        {menuFor === s.id && (
                          <div
                            className={`class-menu ${shown.length > 3 && i >= shown.length - 2 ? 'up' : ''}`}
                            role="menu"
                          >
                            <button role="menuitem" className="class-menu-item" onClick={() => openHistory(s)}>
                              Attendance history
                            </button>
                            <button role="menuitem" className="class-menu-item" onClick={() => openEdit(s)}>
                              Edit name or ID
                            </button>
                            <button role="menuitem" className="class-menu-item danger" onClick={() => remove(s)}>
                              Remove from class
                            </button>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      {historyFor && (
        <Modal title={historyFor.name} onClose={() => setHistoryFor(null)} width={560}>
          <div className="history-summary">
            <span><strong style={{ color: 'var(--present)' }}>{historyFor.present}</strong> present</span>
            <span><strong style={{ color: 'var(--warn)' }}>{historyFor.late}</strong> late</span>
            <span><strong style={{ color: 'var(--missing)' }}>{historyFor.absent}</strong> absent</span>
            <span>Joined {formatDay(historyFor.joined_at)}</span>
          </div>
          <div className="reuse-list">
            {history === null ? (
              <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}><div className="spinner" /></div>
            ) : history.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--muted)', fontSize: 12.5 }}>
                No sessions have been held for this class yet.
              </div>
            ) : (
              history.map((h) => (
                <div key={h.session_id} className="card-row" style={h.counted ? undefined : { opacity: 0.6 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--ink-heading)' }}>{h.name || 'Untitled session'}</div>
                    <div className="reuse-row-sub">
                      {formatDateTime(h.started_at)}
                      {h.time_in && <> · in {formatTime(h.time_in)}</>}
                      {h.time_out && <> · out {formatTime(h.time_out)}</>}
                    </div>
                  </div>
                  {h.status === 'Present' && <span className="badge badge-present">Present</span>}
                  {h.status === 'Late' && <span className="badge badge-warn">Late</span>}
                  {h.status === 'Absent' && <span className="badge badge-missing">Absent</span>}
                  {h.status === null && (
                    <span className="badge" style={{ color: 'var(--muted)', border: '1px solid var(--pill-border)' }}>
                      {h.ended_at ? 'Not in class yet' : 'In progress'}
                    </span>
                  )}
                </div>
              ))
            )}
          </div>
          <p style={{ fontSize: 11.5, color: 'var(--muted-lo)' }}>
            To change a status, open the session on the Reports page.
          </p>
        </Modal>
      )}

      {editing && (
        <Modal title="Edit student" onClose={() => setEditing(null)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label className="field-label" htmlFor="edit-no">Student ID number</label>
              <input
                id="edit-no"
                className="input"
                value={editForm.student_no}
                autoFocus
                onChange={(e) => { setEditForm((f) => ({ ...f, student_no: e.target.value })); setEditError('') }}
                onKeyDown={(e) => { if (e.key === 'Enter' && !editBusy) saveEdit() }}
              />
            </div>
            <div>
              <label className="field-label" htmlFor="edit-name">Full name</label>
              <input
                id="edit-name"
                className="input"
                value={editForm.name}
                onChange={(e) => { setEditForm((f) => ({ ...f, name: e.target.value })); setEditError('') }}
                onKeyDown={(e) => { if (e.key === 'Enter' && !editBusy) saveEdit() }}
              />
            </div>
            <p style={{ fontSize: 11.5, color: 'var(--muted)' }}>
              Face data stays the same. The change shows in every class this student is in.
            </p>
            {editError && <div className="settings-msg settings-msg-error">{editError}</div>}
          </div>
          <div className="settings-footer" style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
            <button className="btn-primary" onClick={saveEdit} disabled={editBusy}>
              {editBusy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </Modal>
      )}

      {reuseOpen && (
        <ReuseStudentsModal
          classInfo={classInfo}
          onClose={() => setReuseOpen(false)}
          onAdded={(name) => { setNotice({ text: `Added ${name} using their saved face data.` }); load() }}
        />
      )}
    </>
  )
}
