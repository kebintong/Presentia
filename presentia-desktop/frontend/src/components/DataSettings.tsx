import { useCallback, useEffect, useState } from 'react'
import Modal from './Modal'
import { redact } from '../diagnostics'

const API = 'http://127.0.0.1:7788'

interface Summary {
  classes: number
  students: number
  sessions: number
  attendance: number
  pending: number
  database_bytes: number
  data_folder: string
}

const size = (b: number) =>
  b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1048576).toFixed(1)} MB`

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/**
 * Settings → Data: what is stored on this computer, and deleting all of it.
 */
export default function DataSettings() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/data/summary`)
      if (!res.ok) throw new Error()
      setSummary(await res.json())
      setError('')
    } catch {
      setError('The Presentia engine is not answering, so the stored data cannot be shown.')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const deleteAll = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/data/delete-all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: typed }),
      })
      const body = await res.json().catch(() => null)
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `HTTP ${res.status}`)
      const problems: string[] = body.website_problems || []
      setDone(
        problems.length
          ? 'All data on this computer was deleted. Some classes could not be removed from the registration ' +
            'website; anything waiting there is deleted automatically within 14 days.'
          : 'All data on this computer was deleted.'
      )
      // Every page holds classes and students in memory: start fresh.
      setTimeout(() => window.location.reload(), 2500)
    } catch (err: any) {
      setError(`Nothing was deleted: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  const empty = summary && summary.classes + summary.students + summary.sessions + summary.pending === 0

  return (
    <div className="settings-section">
      <span className="field-label">Stored on this computer</span>
      {error && <div className="settings-msg settings-msg-error" role="alert">{error}</div>}
      {summary && (
        <dl className="diag-kv">
          <dt>Classes</dt><dd>{summary.classes}</dd>
          <dt>Students</dt><dd>{summary.students} (with face data)</dd>
          <dt>Sessions</dt><dd>{summary.sessions} ({plural(summary.attendance, 'attendance record')})</dd>
          <dt>Waiting registrations</dt><dd>{summary.pending}</dd>
          <dt>Database size</dt><dd>{size(summary.database_bytes)}</dd>
          <dt>Folder</dt><dd>{redact(summary.data_folder)}</dd>
        </dl>
      )}

      <span className="field-label">Danger zone</span>
      <div className="settings-row danger-row">
        <div>
          <div className="settings-row-title">Delete all data</div>
          <div className="settings-row-sub">
            Every class, student and their face data, session and attendance record on this computer.
            This cannot be undone.
          </div>
        </div>
        <button
          className="btn-danger"
          onClick={() => { setTyped(''); setConfirming(true) }}
          disabled={!summary || !!empty}
          title={empty ? 'There is nothing to delete' : undefined}
        >
          Delete all data…
        </button>
      </div>

      {confirming && (
        <Modal title="Delete all data?" onClose={() => !busy && setConfirming(false)} width={500}>
          {done ? (
            <div className="settings-section">
              <div className="settings-msg" role="status">{done}</div>
              <span className="settings-row-sub" style={{ margin: 0 }}>Presentia is restarting its screens…</span>
            </div>
          ) : (
            <div className="settings-section diag-confirm">
              <div>
                <div className="settings-row-title">This deletes, on this computer</div>
                <ul>
                  <li><strong>{plural(summary?.classes ?? 0, 'class', 'classes')}</strong> and their join codes</li>
                  <li><strong>{plural(summary?.students ?? 0, 'student')}</strong>, including their <strong>face data</strong></li>
                  <li><strong>{plural(summary?.sessions ?? 0, 'session')}</strong> and{' '}
                    <strong>{plural(summary?.attendance ?? 0, 'attendance record')}</strong></li>
                  <li>Waiting online registrations, and the diagnostic activity log</li>
                </ul>
              </div>
              <div>
                <div className="settings-row-title">What stays</div>
                <ul>
                  <li>Your settings (theme, performance, the registration website address)</li>
                  <li>Excel or CSV files you exported earlier. Export a class first if you need its records.</li>
                </ul>
              </div>
              <p>
                Classes taking online registrations are also removed from the registration website.
                <strong> This cannot be undone.</strong>
              </p>
              <label className="field-label" htmlFor="delete-confirm" style={{ margin: 0 }}>
                Type DELETE to confirm
              </label>
              <input
                id="delete-confirm"
                className="input mono"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoComplete="off"
                autoFocus
                placeholder="DELETE"
              />
              {error && <div className="settings-msg settings-msg-error" role="alert">{error}</div>}
              <div className="diag-actions">
                <button className="btn-ghost" onClick={() => setConfirming(false)} disabled={busy}>Cancel</button>
                <button
                  className="btn-danger"
                  onClick={deleteAll}
                  disabled={busy || typed.trim().toUpperCase() !== 'DELETE'}
                >
                  {busy ? 'Deleting…' : 'Delete everything'}
                </button>
              </div>
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}
