import React, { useCallback, useEffect, useRef, useState } from 'react'
import Modal from '../components/Modal'
import { ClassInfo, formatJoinCode, shortDate } from '../classes'

const API = 'http://127.0.0.1:7788'

interface ClassPickerPageProps {
  /** Enter a class — the rest of the app then works on its roster. */
  onOpen: (cls: ClassInfo) => void
}

interface ClassForm {
  name: string
  section: string
}

const EMPTY_FORM: ClassForm = { name: '', section: '' }

/** Pull the sidecar's error text out of a failed response. */
async function errorText(res: Response): Promise<string> {
  const body = await res.json().catch(() => null)
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return String(detail.message)
  return `Request failed (HTTP ${res.status})`
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  // Course codes like "IT 101" or "CS50" read better as their letters.
  const code = words[0].match(/^[A-Za-z]{2,4}(?=\d|$)/)
  if (code && words[0] === words[0].toUpperCase()) return code[0].slice(0, 2)
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}

/**
 * Start screen, shown every time the app opens: start a new class or open a
 * past one, like the Google Classroom home page. Opening a past class reuses
 * its roster, so nobody registers their face again.
 */
export default function ClassPickerPage({ onOpen }: ClassPickerPageProps) {
  // null while the sidecar is still starting and nothing has loaded yet.
  const [classes, setClasses] = useState<ClassInfo[] | null>(null)
  const [query, setQuery] = useState('')
  const [menuFor, setMenuFor] = useState<number | null>(null)

  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<ClassInfo | null>(null)
  const [deleting, setDeleting] = useState<ClassInfo | null>(null)
  const [form, setForm] = useState<ClassForm>(EMPTY_FORM)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async (): Promise<boolean> => {
    try {
      const res = await fetch(`${API}/api/classes`)
      if (!res.ok) return false
      setClasses(await res.json())
      return true
    } catch {
      return false // sidecar still starting
    }
  }, [])

  // Keep trying until the sidecar answers; it can take a few seconds on
  // first launch.
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      while (!cancelled) {
        if (await load()) return
        await new Promise((r) => setTimeout(r, 1500))
      }
    }
    run()
    return () => { cancelled = true }
  }, [load])

  // Close the ⋯ menu on any outside click.
  const menuRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (menuFor === null) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuFor(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuFor])

  const closeDialogs = useCallback(() => {
    setCreating(false)
    setRenaming(null)
    setDeleting(null)
    setForm(EMPTY_FORM)
    setFormError('')
    setBusy(false)
  }, [])

  const openCreate = () => {
    closeDialogs()
    setCreating(true)
  }

  const openRename = (c: ClassInfo) => {
    closeDialogs()
    setMenuFor(null)
    setForm({ name: c.name, section: c.section })
    setRenaming(c)
  }

  const openDelete = (c: ClassInfo) => {
    closeDialogs()
    setMenuFor(null)
    setDeleting(c)
  }

  const submitCreate = async () => {
    if (!form.name.trim()) {
      setFormError('Give the class a name.')
      return
    }
    setBusy(true)
    setFormError('')
    try {
      const res = await fetch(`${API}/api/classes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: form.name.trim(), section: form.section.trim() }),
      })
      if (!res.ok) {
        setFormError(await errorText(res))
        setBusy(false)
        return
      }
      const created: ClassInfo = await res.json()
      closeDialogs()
      onOpen(created)
    } catch (err: any) {
      setFormError(err?.message || 'Could not reach the Presentia engine.')
      setBusy(false)
    }
  }

  const submitRename = async () => {
    if (!renaming) return
    if (!form.name.trim()) {
      setFormError('The class needs a name.')
      return
    }
    setBusy(true)
    setFormError('')
    try {
      const res = await fetch(`${API}/api/classes/${renaming.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: form.name.trim(), section: form.section.trim() }),
      })
      if (!res.ok) {
        setFormError(await errorText(res))
        setBusy(false)
        return
      }
      closeDialogs()
      load()
    } catch (err: any) {
      setFormError(err?.message || 'Could not reach the Presentia engine.')
      setBusy(false)
    }
  }

  const submitDelete = async () => {
    if (!deleting) return
    setBusy(true)
    setFormError('')
    try {
      const res = await fetch(`${API}/api/classes/${deleting.id}`, { method: 'DELETE' })
      if (!res.ok) {
        setFormError(await errorText(res))
        setBusy(false)
        return
      }
      const { students_removed } = await res.json()
      const name = deleting.name
      closeDialogs()
      setNotice(
        students_removed > 0
          ? `Deleted "${name}" and ${students_removed} student${students_removed === 1 ? '' : 's'} who were in no other class.`
          : `Deleted "${name}".`
      )
      load()
    } catch (err: any) {
      setFormError(err?.message || 'Could not reach the Presentia engine.')
      setBusy(false)
    }
  }

  const q = query.trim().toLowerCase()
  const visible = (classes ?? []).filter(
    (c) =>
      !q ||
      c.name.toLowerCase().includes(q) ||
      c.section.toLowerCase().includes(q) ||
      c.join_code.toLowerCase().includes(q.replace('-', ''))
  )

  const onFormKey = (submit: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !busy) submit()
  }

  const classFields = (submit: () => void) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <label className="field-label" htmlFor="class-name">Class name</label>
        <input
          id="class-name"
          className={`input ${formError && !form.name.trim() ? 'input-error' : ''}`}
          placeholder="e.g. IT 101 — Introduction to Computing"
          value={form.name}
          maxLength={80}
          autoFocus
          onChange={(e) => { setForm((f) => ({ ...f, name: e.target.value })); setFormError('') }}
          onKeyDown={onFormKey(submit)}
        />
      </div>
      <div>
        <label className="field-label" htmlFor="class-section">Section or block (optional)</label>
        <input
          id="class-section"
          className="input"
          placeholder="e.g. BSIT 2A"
          value={form.section}
          maxLength={80}
          onChange={(e) => setForm((f) => ({ ...f, section: e.target.value }))}
          onKeyDown={onFormKey(submit)}
        />
      </div>
      {formError && <div className="settings-msg settings-msg-error">{formError}</div>}
    </div>
  )

  return (
    <>
      <section className="hero-banner">
        <div className="hero-top-row">
          <div className="hero-title-group">
            <h1 className="hero-title">Your classes</h1>
            <p className="hero-subtitle">
              Open a class to take attendance, or start a new one. Students register once per class
              and are recognised in every session after that.
            </p>
          </div>
          <div className="hero-action-cluster">
            <button className="btn-hero-launch" onClick={openCreate}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
              New class
            </button>
          </div>
        </div>

        {notice && (
          <div className="hero-tip-banner" role="status">
            <span style={{ flex: 1 }}>{notice}</span>
            <button className="btn-ghost" style={{ padding: '3px 10px', fontSize: 11.5 }} onClick={() => setNotice('')}>
              Dismiss
            </button>
          </div>
        )}

        {classes && classes.length > 6 && (
          <input
            className="input class-search"
            placeholder="Search classes by name, section or join code…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        )}
      </section>

      {classes === null ? (
        <div className="picker-empty">
          <div className="spinner" />
          <p>Starting Presentia…</p>
        </div>
      ) : classes.length === 0 ? (
        <div className="picker-empty">
          <div className="picker-empty-icon" aria-hidden="true">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 10 12 5 2 10l10 5 10-5z" />
              <path d="M6 12v5c3 3 9 3 12 0v-5" />
            </svg>
          </div>
          <h2>Create your first class</h2>
          <p>
            A class keeps its own list of students and every attendance session you hold for it.
            Next time, just pick it here and start monitoring.
          </p>
          <button className="btn-primary" onClick={openCreate}>New class</button>
        </div>
      ) : (
        <section className="class-grid" aria-label="Your classes">
          {visible.map((c) => (
            <div key={c.id} className="class-card">
              <button className="class-card-main" onClick={() => onOpen(c)} title={`Open ${c.name}`}>
                <div className="class-card-top">
                  <span className="class-avatar" aria-hidden="true">{initials(c.name)}</span>
                  <span className="class-code-chip" title={c.online
                    ? 'Join code — online registration is on'
                    : 'Join code — turn on online registration on the Students page'}>
                    {c.online ? <span className="class-online-dot" aria-label="Online registration on" /> : null}
                    {formatJoinCode(c.join_code)}
                  </span>
                </div>
                <div className="class-card-title">{c.name}</div>
                <div className="class-card-section">{c.section || 'No section'}</div>
                <div className="class-card-meta">
                  <span>{c.student_count} student{c.student_count === 1 ? '' : 's'}</span>
                  <span aria-hidden="true">·</span>
                  <span>{c.session_count} session{c.session_count === 1 ? '' : 's'}</span>
                  {!!c.pending_count && (
                    <>
                      <span aria-hidden="true">·</span>
                      <span className="class-pending">{c.pending_count} to accept</span>
                    </>
                  )}
                  {c.last_session_at && (
                    <>
                      <span aria-hidden="true">·</span>
                      <span>Last session {shortDate(c.last_session_at)}</span>
                    </>
                  )}
                </div>
              </button>

              <div className="class-menu-anchor" ref={menuFor === c.id ? menuRef : undefined}>
                <button
                  className="class-menu-btn"
                  aria-label={`Options for ${c.name}`}
                  aria-haspopup="menu"
                  aria-expanded={menuFor === c.id}
                  onClick={() => setMenuFor(menuFor === c.id ? null : c.id)}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" />
                  </svg>
                </button>
                {menuFor === c.id && (
                  <div className="class-menu" role="menu">
                    <button role="menuitem" className="class-menu-item" onClick={() => openRename(c)}>
                      Rename
                    </button>
                    <button role="menuitem" className="class-menu-item danger" onClick={() => openDelete(c)}>
                      Delete class
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
          {visible.length === 0 && (
            <div className="picker-no-match">No class matches “{query}”.</div>
          )}
        </section>
      )}

      {creating && (
        <Modal title="New class" onClose={closeDialogs}>
          {classFields(submitCreate)}
          <div className="settings-footer" style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn-ghost" onClick={closeDialogs}>Cancel</button>
            <button className="btn-primary" onClick={submitCreate} disabled={busy}>
              {busy ? 'Creating…' : 'Create and open'}
            </button>
          </div>
        </Modal>
      )}

      {renaming && (
        <Modal title="Rename class" onClose={closeDialogs}>
          {classFields(submitRename)}
          <div className="settings-footer" style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn-ghost" onClick={closeDialogs}>Cancel</button>
            <button className="btn-primary" onClick={submitRename} disabled={busy}>
              {busy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </Modal>
      )}

      {deleting && (
        <Modal title={`Delete “${deleting.name}”?`} onClose={closeDialogs}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13, color: 'var(--ink)', lineHeight: 1.55 }}>
            <p>
              This permanently deletes the class, its {deleting.session_count} session
              {deleting.session_count === 1 ? '' : 's'} and all of their attendance records.
            </p>
            <p style={{ color: 'var(--muted)' }}>
              Students who are not in any of your other classes are deleted too, including their
              face data. Students who are also in another class stay there.
            </p>
            {formError && <div className="settings-msg settings-msg-error">{formError}</div>}
          </div>
          <div className="settings-footer" style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn-ghost" onClick={closeDialogs} autoFocus>Cancel</button>
            <button className="btn-danger" onClick={submitDelete} disabled={busy}>
              {busy ? 'Deleting…' : 'Delete class'}
            </button>
          </div>
        </Modal>
      )}
    </>
  )
}
