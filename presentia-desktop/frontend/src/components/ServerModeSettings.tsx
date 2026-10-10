import { useCallback, useEffect, useState } from 'react'
import Modal from './Modal'

const API = 'http://127.0.0.1:7788'

interface Teacher {
  id: number
  username: string
  name: string
  disabled: boolean
  last_login: string | null
  class_ids: number[]
}

interface Status {
  enabled: boolean
  running: boolean
  error: string
  port: number
  signed_in: number
  monitoring: number
  max_classes: number
  local_url: string
  tunnel_command: string
  teachers: Teacher[]
}

interface ClassInfo { id: number; name: string; section: string }

interface Form {
  id: number | null
  name: string
  username: string
  password: string
  classIds: number[]
}

const emptyForm: Form = { id: null, name: '', username: '', password: '', classIds: [] }

// A password teachers can type: four groups of letters and numbers.
function newPassword(): string {
  const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  const bytes = new Uint32Array(16)
  crypto.getRandomValues(bytes)
  const s = Array.from(bytes, (b) => chars[b % chars.length]).join('')
  return `${s.slice(0, 4)}-${s.slice(4, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}`
}

async function call(path: string, method = 'GET', body?: unknown) {
  const res = await fetch(`${API}${path}`, {
    method, headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`)
  return data
}

/**
 * Settings → Server mode: teachers use Presentia from their own browser,
 * through a tunnel to this computer (app/gateway.py).
 */
export default function ServerModeSettings() {
  const [st, setSt] = useState<Status | null>(null)
  const [classes, setClasses] = useState<ClassInfo[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState<Form | null>(null)
  const [formError, setFormError] = useState('')
  const [shown, setShown] = useState<{ name: string; username: string; password: string } | null>(null)
  const [copied, setCopied] = useState('')

  const load = useCallback(async () => {
    try {
      setSt(await call('/api/server-mode'))
      setError('')
    } catch {
      setError('The Presentia engine is not answering.')
    }
  }, [])

  useEffect(() => {
    load()
    call('/api/classes').then(setClasses).catch(() => {})
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [load])

  const toggle = async () => {
    if (!st) return
    setBusy(true)
    try { setSt(await call('/api/server-mode', 'PUT', { enabled: !st.enabled })) }
    catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const copy = async (text: string, what: string) => {
    try {
      const rt = (window as any)['runtime']
      if (rt?.ClipboardSetText) await rt.ClipboardSetText(text)
      else await navigator.clipboard.writeText(text)
      setCopied(what)
      setTimeout(() => setCopied(''), 1800)
    } catch { /* ignore */ }
  }

  const save = async () => {
    if (!form) return
    setFormError('')
    try {
      if (form.id == null) {
        await call('/api/teachers', 'POST', { username: form.username, name: form.name, password: form.password,
                                              class_ids: form.classIds })
        setShown({ name: form.name, username: form.username.trim().toLowerCase(), password: form.password })
      } else {
        await call(`/api/teachers/${form.id}`, 'PATCH', {
          name: form.name, class_ids: form.classIds, ...(form.password ? { password: form.password } : {}),
        })
        if (form.password) setShown({ name: form.name, username: form.username, password: form.password })
      }
      setForm(null)
      load()
    } catch (e: any) {
      setFormError(e.message)
    }
  }

  const setDisabled = async (t: Teacher, disabled: boolean) => {
    try { await call(`/api/teachers/${t.id}`, 'PATCH', { disabled }); load() } catch (e: any) { setError(e.message) }
  }

  const remove = async (t: Teacher) => {
    if (!window.confirm(`Delete ${t.name}'s account? They will be signed out and can no longer sign in.`)) return
    try { await call(`/api/teachers/${t.id}`, 'DELETE'); load() } catch (e: any) { setError(e.message) }
  }

  const className = (id: number) => {
    const c = classes.find((x) => x.id === id)
    return c ? c.name + (c.section ? ` · ${c.section}` : '') : `Class ${id}`
  }

  return (
    <div className="settings-section">
      <span className="field-label">Server mode</span>
      {error && <div className="settings-msg settings-msg-error" role="alert">{error}</div>}

      <div className="settings-row">
        <div>
          <div className="settings-row-title">Let teachers use Presentia from their browser</div>
          <div className="settings-row-sub">
            This computer becomes the Presentia server. Teachers sign in with an account you make below, share their
            meeting tab, and see their roster live — the face checks run here, on this computer's graphics card.
            Presentia must stay open (and the computer awake) while classes are monitored.
          </div>
        </div>
        <button type="button" role="switch" aria-checked={!!st?.enabled} aria-label="Server mode"
          className={`toggle-switch ${st?.enabled ? 'on' : ''}`} disabled={!st || busy} onClick={toggle}>
          <span className="toggle-knob" />
        </button>
      </div>

      {st?.enabled && (
        <div className="server-box">
          {st.running ? (
            <>
              <div className="server-line">
                <span className="tabshare-dot server-ok" />
                <span>Running on <strong>{st.local_url}</strong> · {st.signed_in} signed in ·
                  {' '}{st.monitoring} of {st.max_classes} classes being monitored</span>
              </div>
              <div className="settings-row-sub" style={{ marginTop: 8 }}>
                To reach it from the internet, run this on this computer (Cloudflare Tunnel) and give teachers the
                <strong> https://…trycloudflare.com</strong> address it prints:
              </div>
              <div className="server-cmd">
                <code>{st.tunnel_command}</code>
                <button className="btn-ghost" onClick={() => copy(st.tunnel_command, 'cmd')}>
                  {copied === 'cmd' ? 'Copied ✓' : 'Copy'}
                </button>
              </div>
              <div className="settings-warn">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" style={{ flexShrink: 0, marginTop: 1 }}>
                  <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" />
                </svg>
                <span>Only ever share port {st.port}. Never put port 7788 (the engine) on the internet — it has no
                  sign-in. Students' faces from shared tabs are processed on this computer; tell students and get
                  their consent.</span>
              </div>
            </>
          ) : (
            <div className="settings-msg settings-msg-error">
              Server mode could not start{st.error ? `: ${st.error}` : ''}. Is another program using port {st.port}?
            </div>
          )}
        </div>
      )}

      <div className="settings-row" style={{ alignItems: 'center' }}>
        <div>
          <div className="settings-row-title">Teacher accounts</div>
          <div className="settings-row-sub">Each teacher can monitor only the classes you give them.</div>
        </div>
        <button className="btn-primary" onClick={() => { setFormError(''); setForm({ ...emptyForm, password: newPassword() }) }}>
          Add teacher
        </button>
      </div>

      <div className="teacher-list">
        {st && st.teachers.length === 0 && <div className="settings-row-sub">No teacher accounts yet.</div>}
        {st?.teachers.map((t) => (
          <div key={t.id} className={`teacher-row ${t.disabled ? 'off' : ''}`}>
            <div style={{ minWidth: 0 }}>
              <div className="settings-row-title">{t.name} <span className="teacher-user">@{t.username}</span>
                {t.disabled && <span className="badge badge-warn" style={{ marginLeft: 6 }}>OFF</span>}</div>
              <div className="settings-row-sub">
                {t.class_ids.length ? t.class_ids.map(className).join(', ') : 'No classes'}
                {' · '}{t.last_login ? `last signed in ${t.last_login.slice(0, 16)}` : 'never signed in'}
              </div>
            </div>
            <div className="teacher-actions">
              <button className="btn-ghost" onClick={() => { setFormError(''); setForm({ id: t.id, name: t.name, username: t.username, password: '', classIds: t.class_ids }) }}>Edit</button>
              <button className="btn-ghost" onClick={() => setDisabled(t, !t.disabled)}>{t.disabled ? 'Turn on' : 'Turn off'}</button>
              <button className="btn-ghost" onClick={() => remove(t)}>Delete</button>
            </div>
          </div>
        ))}
      </div>

      {form && (
        <Modal title={form.id == null ? 'Add a teacher' : `Edit ${form.name}`} onClose={() => setForm(null)} width={460}>
          <div className="check-body">
            <div>
              <label className="field-label">Name</label>
              <input className="input" value={form.name} placeholder="e.g. Ana Reyes"
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <label className="field-label">Username</label>
              <input className="input" value={form.username} placeholder="e.g. ana.reyes" disabled={form.id != null}
                onChange={(e) => setForm({ ...form, username: e.target.value })} />
            </div>
            <div>
              <label className="field-label">{form.id == null ? 'Password' : 'New password (leave empty to keep)'}</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input className="input" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
                <button className="btn-ghost" onClick={() => setForm({ ...form, password: newPassword() })}>New</button>
              </div>
            </div>
            <div>
              <label className="field-label">Classes they can monitor</label>
              <div className="teacher-classes">
                {classes.length === 0 && <span className="check-muted">No classes yet.</span>}
                {classes.map((c) => (
                  <label key={c.id} className="teacher-class">
                    <input type="checkbox" checked={form.classIds.includes(c.id)}
                      onChange={(e) => setForm({ ...form, classIds: e.target.checked
                        ? [...form.classIds, c.id] : form.classIds.filter((x) => x !== c.id) })} />
                    {c.name}{c.section ? ` · ${c.section}` : ''}
                  </label>
                ))}
              </div>
            </div>
            {formError && <div className="check-note warn">{formError}</div>}
            <div className="check-actions">
              <button className="btn-ghost" onClick={() => setForm(null)}>Cancel</button>
              <button className="btn-primary" onClick={save}>{form.id == null ? 'Add teacher' : 'Save'}</button>
            </div>
          </div>
        </Modal>
      )}

      {shown && (
        <Modal title="Give these to the teacher" onClose={() => setShown(null)} width={420}>
          <div className="check-body">
            <p>Send {shown.name} their sign-in privately. The password is shown only now.</p>
            <dl className="diag-kv">
              <dt>Username</dt><dd><code>{shown.username}</code></dd>
              <dt>Password</dt><dd><code>{shown.password}</code></dd>
            </dl>
            <div className="check-actions">
              <button className="btn-ghost" onClick={() => copy(`Presentia sign-in\nUsername: ${shown.username}\nPassword: ${shown.password}`, 'pw')}>
                {copied === 'pw' ? 'Copied ✓' : 'Copy'}
              </button>
              <button className="btn-primary" onClick={() => setShown(null)}>Done</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
