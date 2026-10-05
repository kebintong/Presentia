import React, { useCallback, useEffect, useState } from 'react'
import Modal from './Modal'
import { ClassInfo } from '../classes'

const API = 'http://127.0.0.1:7788'

interface ReusableStudent {
  id: number
  student_no: string
  name: string
  /** Names of the classes the student is already in. */
  classes: string
}

interface ReuseStudentsModalProps {
  classInfo: ClassInfo
  onClose: () => void
  /** Called after each student is added, so the caller can refresh. */
  onAdded: (name: string) => void
}

/** "Add from other classes": put students who are already registered in
 *  another class on this roster, reusing their saved face data. */
export default function ReuseStudentsModal({ classInfo, onClose, onAdded }: ReuseStudentsModalProps) {
  const [students, setStudents] = useState<ReusableStudent[] | null>(null)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/classes/${classInfo.id}/available-students`)
      setStudents(res.ok ? await res.json() : [])
    } catch {
      setStudents([])
    }
  }, [classInfo.id])

  useEffect(() => { load() }, [load])

  const add = async (s: ReusableStudent) => {
    setBusy(s.id)
    setError('')
    try {
      const res = await fetch(`${API}/api/classes/${classInfo.id}/students/${s.id}`, { method: 'PUT' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setStudents((list) => (list ?? []).filter((x) => x.id !== s.id))
      onAdded(s.name)
    } catch (err: any) {
      setError(`Could not add ${s.name}: ${err.message}`)
    } finally {
      setBusy(null)
    }
  }

  const q = query.trim().toLowerCase()
  const shown = (students ?? []).filter(
    (s) =>
      !q ||
      s.name.toLowerCase().includes(q) ||
      s.student_no.toLowerCase().includes(q) ||
      s.classes.toLowerCase().includes(q)
  )

  return (
    <Modal title="Add from other classes" onClose={onClose} width={500}>
      <p style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.5 }}>
        Students you already registered in another class can join {classInfo.name} with their
        saved face data — no new capture needed.
      </p>
      <input
        className="input"
        placeholder="Search by name, student number or class…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        autoFocus
        style={{ padding: '8px 12px', fontSize: 12.5 }}
      />
      {error && <div className="settings-msg settings-msg-error">{error}</div>}
      <div className="reuse-list">
        {students === null ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}><div className="spinner" /></div>
        ) : shown.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--muted)', fontSize: 12.5 }}>
            {students.length === 0
              ? 'Everyone you have registered is already in this class.'
              : 'No matching students'}
          </div>
        ) : (
          shown.map((s) => (
            <div key={s.id} className="card-row">
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--ink-heading)' }}>{s.name}</div>
                <div className="reuse-row-sub">
                  <span style={{ fontFamily: 'monospace' }}>{s.student_no}</span>
                  {s.classes && <> · {s.classes}</>}
                </div>
              </div>
              <button
                className="btn-primary"
                style={{ padding: '5px 12px', fontSize: 11.5 }}
                disabled={busy !== null}
                onClick={() => add(s)}
              >
                {busy === s.id ? 'Adding…' : 'Add'}
              </button>
            </div>
          ))
        )}
      </div>
      <div className="settings-footer" style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button className="btn-ghost" onClick={onClose}>Done</button>
      </div>
    </Modal>
  )
}
