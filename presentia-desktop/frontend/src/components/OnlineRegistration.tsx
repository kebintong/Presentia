import React, { useCallback, useEffect, useRef, useState } from 'react'
import { ClipboardSetText } from '../../wailsjs/runtime/runtime'
import { ClassInfo, formatDateTime, formatJoinCode } from '../classes'

const API = 'http://127.0.0.1:7788'
const AUTO_SYNC_MS = 60_000

interface Pending {
  id: number
  student_no: string
  name: string
  photo_b64: string
  samples: number
  problem: string
  submitted_at: string
  has_face: boolean
  existing_id: number | null
  existing_name: string | null
  existing_in_class: boolean
  /**
   * What Accept does (Google Classroom style: one student, many classes):
   * new      — adds a new student;
   * join     — adds the student already registered on this computer, with
   *            their saved face data (matched by number or by face);
   * in_class — that student is already in this class (nothing to add);
   * conflict — the number is one student's, the face another's;
   * number_taken — the number is a registered student's, the face someone else's;
   * no_face  — new number and no usable face.
   */
  action: 'new' | 'join' | 'in_class' | 'conflict' | 'number_taken' | 'no_face'
  match: { id: number; student_no: string; name: string } | null
  via: 'number' | 'face' | null
  conflict_with: { id: number; student_no: string; name: string } | null
  /** Another waiting registration has the same number but a different face. */
  number_clash: { name: string; submitted_at: string } | null
  /** Same face as another NEW registration that is also waiting. */
  pending_match: { student_no: string; name: string } | null
  /** The student number is on file, but with a different face. */
  face_differs: boolean
}

const canAccept = (p: Pending) => p.action === 'new' || p.action === 'join'

/** Can be accepted without the instructor having to look twice. */
const isClean = (p: Pending) =>
  canAccept(p) && !p.problem && !p.pending_match && !p.number_clash && !p.face_differs

interface Props {
  classInfo: ClassInfo
  /** A student was added to the class (refresh the roster). */
  onRosterChanged: () => void
}

async function errorText(res: Response): Promise<string> {
  const body = await res.json().catch(() => null)
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return String(detail.message)
  return `Request failed (HTTP ${res.status})`
}

async function copyText(text: string): Promise<boolean> {
  try {
    if ((window as any)['runtime']) return await ClipboardSetText(text)
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/**
 * Students page card: let students register themselves on the Presentia
 * website with the class join code, then accept or reject what comes in.
 */
export default function OnlineRegistration({ classInfo, onRosterChanged }: Props) {
  const [url, setUrl] = useState<string | null>(null)   // null = loading
  const [urlDraft, setUrlDraft] = useState('')
  const [editingUrl, setEditingUrl] = useState(false)
  const [online, setOnline] = useState(!!classInfo.online)
  const [code, setCode] = useState(classInfo.join_code)
  const [pending, setPending] = useState<Pending[]>([])
  const [busy, setBusy] = useState<string>('')           // which action is running
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null)
  const [lastSync, setLastSync] = useState<Date | null>(null)
  const [copied, setCopied] = useState(false)
  const syncing = useRef(false)

  const shareLink = url ? `${url}/?code=${code}` : ''

  const loadPending = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/classes/${classInfo.id}/pending`)
      if (res.ok) setPending(await res.json())
    } catch { /* sidecar restarting */ }
  }, [classInfo.id])

  const load = useCallback(async () => {
    try {
      const [cfg, cls] = await Promise.all([
        fetch(`${API}/api/cloud`).then((r) => r.json()),
        fetch(`${API}/api/classes/${classInfo.id}`).then((r) => r.json()),
      ])
      setUrl(cfg.url || '')
      setUrlDraft(cfg.url || '')
      setOnline(!!cls.online)
      setCode(cls.join_code)
    } catch {
      setUrl('')
    }
    loadPending()
  }, [classInfo.id, loadPending])

  useEffect(() => { load() }, [load])

  const sync = useCallback(async (quiet: boolean) => {
    if (syncing.current) return
    syncing.current = true
    if (!quiet) { setBusy('sync'); setMessage(null) }
    try {
      const res = await fetch(`${API}/api/classes/${classInfo.id}/sync`, { method: 'POST' })
      if (!res.ok) throw new Error(await errorText(res))
      const { received } = await res.json()
      setLastSync(new Date())
      if (received > 0) {
        setMessage({ text: `${received} new registration${received === 1 ? '' : 's'} received.` })
      } else if (!quiet) {
        setMessage({ text: 'No new registrations.' })
      }
      loadPending()
    } catch (err: any) {
      if (!quiet) setMessage({ text: err.message, error: true })
    } finally {
      syncing.current = false
      if (!quiet) setBusy('')
    }
  }, [classInfo.id, loadPending])

  // While online: check now, then every minute while this page is open.
  useEffect(() => {
    if (!online) return
    sync(true)
    const t = setInterval(() => sync(true), AUTO_SYNC_MS)
    return () => clearInterval(t)
  }, [online, sync])

  const saveUrl = async () => {
    setBusy('url')
    setMessage(null)
    try {
      const res = await fetch(`${API}/api/cloud`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: urlDraft.trim() }),
      })
      if (!res.ok) throw new Error(await errorText(res))
      const cfg = await res.json()
      setUrl(cfg.url)
      setEditingUrl(false)
      setMessage({ text: 'Website connected.' })
    } catch (err: any) {
      setMessage({ text: err.message, error: true })
    } finally {
      setBusy('')
    }
  }

  const toggle = async (enabled: boolean) => {
    if (!enabled && !confirm(
      'Turn off online registration?\n\nStudents can no longer register with the join code, and ' +
      'registrations still waiting on the website are deleted. Ones already received here stay.'
    )) return
    setBusy('toggle')
    setMessage(null)
    try {
      const res = await fetch(`${API}/api/classes/${classInfo.id}/online`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      if (!res.ok) throw new Error(await errorText(res))
      const cls = await res.json()
      setOnline(!!cls.online)
      setCode(cls.join_code)
      setMessage({
        text: enabled
          ? 'Online registration is on. Share the link or the join code with your students.'
          : 'Online registration is off.',
      })
    } catch (err: any) {
      setMessage({ text: err.message, error: true })
    } finally {
      setBusy('')
    }
  }

  const decide = async (p: Pending, accept: boolean) => {
    setBusy(`p${p.id}`)
    setMessage(null)
    try {
      const res = accept
        ? await fetch(`${API}/api/pending/${p.id}/approve`, { method: 'POST' })
        : await fetch(`${API}/api/pending/${p.id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(await errorText(res))
      if (accept) {
        const out = await res.json()
        setMessage({
          text: out.result === 'linked'
            ? `Added ${out.name} to ${classInfo.name} with their saved face data.`
            : `Added ${out.name} to ${classInfo.name}.`,
        })
        onRosterChanged()
      }
      setPending((list) => list.filter((x) => x.id !== p.id))
      // Accepting one can make another waiting registration a duplicate.
      if (accept) loadPending()
    } catch (err: any) {
      setMessage({ text: err.message, error: true })
    } finally {
      setBusy('')
    }
  }

  const acceptAllReady = async () => {
    // The same existing student can only be added once.
    const seen = new Set<number>()
    const ready = pending.filter(isClean).filter((p) => {
      if (!p.match) return true
      if (seen.has(p.match.id)) return false
      seen.add(p.match.id)
      return true
    })
    for (const p of ready) await decide(p, true)
  }

  const copyLink = async () => {
    setCopied(await copyText(shareLink))
    setTimeout(() => setCopied(false), 2000)
  }

  if (url === null) return null
  const readyCount = pending.filter(isClean).length

  return (
    <section className="launcher-card online-card">
      <div className="card-header">
        <div className="card-header-left">
          <div className="card-icon-badge sapphire">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <path d="M2 12h20M12 2a15 15 0 0 1 4 10 15 15 0 0 1-4 10 15 15 0 0 1-4-10 15 15 0 0 1 4-10z" />
            </svg>
          </div>
          <span className="card-title-text">Online registration</span>
          {online && <span className="badge badge-present">On</span>}
        </div>
        {url && !editingUrl && (
          <button className="card-action-link online-site" onClick={() => setEditingUrl(true)} title={url}>
            Website settings
          </button>
        )}
      </div>

      {(!url || editingUrl) ? (
        <div className="online-setup">
          <p>
            Students can register from their own phone or laptop: they enter the join code, their details,
            and take a short live selfie. You accept them here. First, enter the address of your Presentia
            registration website (set it up once with the guide in <span className="mono">web/README.md</span>).
          </p>
          <div className="online-row">
            <input
              className="input"
              placeholder="https://presentia.your-name.workers.dev"
              value={urlDraft}
              onChange={(e) => setUrlDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && busy !== 'url') saveUrl() }}
            />
            <button className="btn-primary" onClick={saveUrl} disabled={busy === 'url' || !urlDraft.trim()}>
              {busy === 'url' ? 'Checking…' : 'Connect'}
            </button>
            {editingUrl && <button className="btn-ghost" onClick={() => { setEditingUrl(false); setUrlDraft(url || '') }}>Cancel</button>}
          </div>
        </div>
      ) : !online ? (
        <div className="online-row online-off">
          <p>
            Let students in {classInfo.name} register themselves with join code{' '}
            <strong className="mono">{formatJoinCode(code)}</strong>. You accept each one before they are added.
          </p>
          <button className="btn-primary" onClick={() => toggle(true)} disabled={busy === 'toggle'}>
            {busy === 'toggle' ? 'Turning on…' : 'Turn on'}
          </button>
        </div>
      ) : (
        <>
          <div className="online-share">
            <div className="online-code">
              <span className="field-label" style={{ margin: 0 }}>Join code</span>
              <strong className="mono">{formatJoinCode(code)}</strong>
            </div>
            <div className="online-link">
              <span className="field-label" style={{ margin: 0 }}>Registration link</span>
              <span className="mono online-link-text" title={shareLink}>{shareLink}</span>
            </div>
            <div className="online-actions">
              <button className="btn-ghost" onClick={copyLink}>{copied ? 'Copied' : 'Copy link'}</button>
              <button className="btn-ghost" onClick={() => sync(false)} disabled={busy === 'sync'}>
                {busy === 'sync' ? 'Checking…' : 'Check now'}
              </button>
              <button className="btn-ghost online-off-btn" onClick={() => toggle(false)} disabled={busy === 'toggle'}>
                Turn off
              </button>
            </div>
          </div>
          {lastSync && (
            <span className="online-sync-note">
              Checked {lastSync.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })} ·
              checks every minute while this page is open
            </span>
          )}
        </>
      )}

      {message && (
        <div className={`settings-msg ${message.error ? 'settings-msg-error' : ''}`} role="status">{message.text}</div>
      )}

      {pending.length > 0 && (
        <div className="pending-block">
          <div className="pending-head">
            <span className="field-label" style={{ margin: 0 }}>
              {pending.length} waiting for you to accept
            </span>
            {readyCount > 1 && (
              <button className="btn-primary pending-all" onClick={acceptAllReady} disabled={!!busy}>
                Accept all {readyCount} without issues
              </button>
            )}
          </div>
          <div className="pending-list">
            {pending.map((p) => {
              const acceptable = canAccept(p)
              return (
                <div key={p.id} className="pending-row">
                  {p.photo_b64
                    ? <img className="pending-photo" src={`data:image/jpeg;base64,${p.photo_b64}`} alt="" />
                    : <div className="pending-photo pending-photo-empty" aria-hidden="true">?</div>}
                  <div className="pending-info">
                    <div className="pending-name">{p.name}</div>
                    <div className="pending-sub">
                      <span className="mono">{p.student_no}</span> · {formatDateTime(p.submitted_at)}
                    </div>
                    {p.action === 'join' && p.match && (
                      <div className="pending-note">
                        Already registered as {p.match.name} (<span className="mono">{p.match.student_no}</span>)
                        {p.via === 'face' ? ', recognised by face' : ''}. Accept adds them to this class with their
                        saved face data; nothing new is stored.
                        {p.via === 'face' && ` They typed ${p.student_no}; the saved number is kept.`}
                      </div>
                    )}
                    {p.action === 'in_class' && p.match && (
                      <div className="pending-note">
                        {p.match.name} (<span className="mono">{p.match.student_no}</span>) is already in this class
                        {p.via === 'face' ? ' (same face)' : ''}. Dismiss this registration.
                      </div>
                    )}
                    {p.action === 'conflict' && p.match && p.conflict_with && (
                      <div className="pending-note danger">
                        Student number {p.match.student_no} belongs to {p.match.name}, but this face is
                        {' '}{p.conflict_with.name} ({p.conflict_with.student_no}). Reject it and check with the student.
                      </div>
                    )}
                    {p.action === 'number_taken' && p.match && (
                      <div className="pending-note danger">
                        Student number {p.match.student_no} belongs to {p.match.name}, and this is a different
                        person. Reject it and ask them to check their student number.
                      </div>
                    )}
                    {p.number_clash && (
                      <div className="pending-note danger">
                        {p.number_clash.name} also registered with student number {p.student_no}, and it is a
                        different person. Accept only the one this number really belongs to.
                      </div>
                    )}
                    {p.pending_match && (
                      <div className="pending-note warn">
                        Same face as {p.pending_match.name} ({p.pending_match.student_no}), who is also waiting.
                        Accept only the right one.
                      </div>
                    )}
                    {p.face_differs && p.match && p.action !== 'number_taken' && (
                      <div className="pending-note warn">
                        This face does not match the one on file for {p.match.name}. Check it is really them.
                      </div>
                    )}
                    {p.problem && <div className="pending-note warn">{p.problem}</div>}
                  </div>
                  <div className="pending-buttons">
                    <button className="btn-ghost" onClick={() => decide(p, false)} disabled={busy === `p${p.id}`}>
                      {p.action === 'in_class' ? 'Dismiss' : 'Reject'}
                    </button>
                    <button
                      className="btn-primary"
                      onClick={() => decide(p, true)}
                      disabled={busy === `p${p.id}` || !acceptable}
                      title={
                        acceptable ? 'Add to this class'
                          : p.action === 'in_class' ? 'Already in this class'
                          : p.action === 'conflict' || p.action === 'number_taken'
                            ? 'The student number belongs to a different person'
                          : 'No usable face — reject and ask them to register again'
                      }
                    >
                      Accept
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
