import { useCallback, useEffect, useState } from 'react'
import { API } from '../api'

interface Account {
  configured: boolean
  signed_in: boolean
  email: string
  last_sync: string
  error: string
}

const detail = async (res: Response) => {
  const body = await res.json().catch(() => null)
  return typeof body?.detail === 'string' ? body.detail : `Something went wrong (${res.status}).`
}

/**
 * Settings → Phone app: sign in so the Presentia phone app sees this
 * computer's classes, takes in-person attendance and decides registrations.
 * Optional; without it everything stays on this computer.
 */
export default function PhoneSettings() {
  const [account, setAccount] = useState<Account | null>(null)
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [codeSent, setCodeSent] = useState(false)
  const [resendAt, setResendAt] = useState(0)
  const [now, setNow] = useState(Date.now())
  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/account`)
      if (res.ok) setAccount(await res.json())
    } catch {
      setError('The Presentia engine is not answering.')
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 10_000)
    return () => clearInterval(t)
  }, [load])

  // Supabase allows one code per email every 60 seconds.
  useEffect(() => {
    if (resendAt <= Date.now()) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [resendAt])
  const wait = Math.max(0, Math.ceil((resendAt - now) / 1000))

  const sendCode = async () => {
    await post('/api/account/code', { email })
    // Show the code box even if this send was refused: a code from an earlier email still works.
    setCodeSent(true)
    setResendAt(Date.now() + 60_000)
    setNow(Date.now())
  }

  const post = async (path: string, body?: object) => {
    setBusy(true)
    setError('')
    try {
      const res = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
      if (!res.ok) throw new Error(await detail(res))
      if (res.status !== 204) setAccount(await res.json())
      return true
    } catch (err: any) {
      setError(err.message || 'Could not reach the Presentia engine.')
      return false
    } finally {
      setBusy(false)
    }
  }

  if (!account) {
    return <div className="settings-section">{error && <div className="settings-msg settings-msg-error" role="alert">{error}</div>}</div>
  }

  if (!account.configured) {
    return (
      <div className="settings-section">
        <div className="settings-msg">The phone app is not set up in this copy of Presentia.</div>
      </div>
    )
  }

  if (account.signed_in) {
    return (
      <div className="settings-section">
        <span className="field-label">Phone app</span>
        <dl className="diag-kv">
          <dt>Signed in as</dt><dd>{account.email}</dd>
          <dt>Last synced</dt><dd>{account.last_sync || 'Not yet'}</dd>
        </dl>
        {(error || account.error) && (
          <div className="settings-msg settings-msg-error" role="alert">{error || account.error}</div>
        )}
        <div className="settings-row-sub">
          Sign in to the Presentia phone app with the same email. Classes, students, attendance and
          waiting registrations stay in step every few seconds; accept a registration on either one.
        </div>
        <div className="diag-actions">
          <button className="btn-ghost" disabled={busy} onClick={() => post('/api/account/sign-out')}>Sign out</button>
          <button className="btn-primary" disabled={busy} onClick={() => post('/api/account/sync')}>
            {busy ? 'Syncing…' : 'Sync now'}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="settings-section">
      <span className="field-label">Phone app</span>
      <div className="settings-row-sub">
        Use the Presentia phone app for in-person attendance, random recitation and accepting
        registrations. Presentia works without it, and nothing leaves this computer until you sign in.
      </div>
      <div className="settings-warn">
        <span>
          Signing in copies your classes, students <strong>including their face data</strong>, attendance and
          waiting registrations to your Presentia account online, so the phone app can use them. Signing out
          later stops the copying. Deleting a class or student here also deletes it there.
        </span>
      </div>
      <label className="settings-row-sub" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
        I understand, and my school allows it.
      </label>
      <label className="field-label" htmlFor="phone-email" style={{ margin: 0 }}>Email</label>
      <input
        id="phone-email"
        className="input"
        type="email"
        value={email}
        onChange={(e) => { setEmail(e.target.value); setCodeSent(false) }}
        autoComplete="email"
        placeholder="you@school.edu.ph"
      />
      {codeSent && (
        <>
          <label className="field-label" htmlFor="phone-code" style={{ margin: 0 }}>
            Code from the email
          </label>
          <input
            id="phone-code"
            className="input mono"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 10))}
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
          />
        </>
      )}
      {error && <div className="settings-msg settings-msg-error" role="alert">{error}</div>}
      <div className="diag-actions">
        {codeSent ? (
          <>
            <button className="btn-ghost" disabled={busy || wait > 0} onClick={sendCode}>
              {wait > 0 ? `Send a new code (${wait} s)` : 'Send a new code'}
            </button>
            <button className="btn-primary" disabled={busy || code.length < 6}
                    onClick={() => post('/api/account/verify', { email, code })}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </>
        ) : (
          <button className="btn-primary" disabled={busy || !agreed || !email.includes('@')}
                  onClick={sendCode}>
            {busy ? 'Sending…' : 'Email me a code'}
          </button>
        )}
      </div>
    </div>
  )
}
