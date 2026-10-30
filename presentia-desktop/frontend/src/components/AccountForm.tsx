import { useEffect, useState } from 'react'
import { API } from '../api'

export interface Account {
  configured: boolean
  signed_in: boolean
  email: string
  last_sync: string
  error: string
  needs_code?: boolean
}

type Mode = 'sign-in' | 'sign-up' | 'confirm' | 'forgot' | 'reset'

const PASSWORD_MIN = 8   // same as the engine (PASSWORD_MIN in app/sidecar.py)

const detail = async (res: Response) => {
  const body = await res.json().catch(() => null)
  return typeof body?.detail === 'string' ? body.detail : `Something went wrong (${res.status}).`
}

/**
 * The phone app account: sign in with email and password, or create one
 * (a code is emailed once to confirm the address), or reset a forgotten
 * password with an emailed code. Used by the first-start screens and by
 * the Phone App page (sidebar).
 */
export default function AccountForm({ onSignedIn }: { onSignedIn: (account: Account) => void }) {
  const [mode, setMode] = useState<Mode>('sign-in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [resendAt, setResendAt] = useState(0)
  const [now, setNow] = useState(Date.now())

  // Supabase sends one email per address every 60 seconds.
  useEffect(() => {
    if (resendAt <= Date.now()) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [resendAt])
  const wait = Math.max(0, Math.ceil((resendAt - now) / 1000))
  const emailed = () => { setResendAt(Date.now() + 60_000); setNow(Date.now()) }

  const go = (next: Mode, message = '') => { setMode(next); setError(''); setNote(message); setCode('') }

  /** The account (signed in or not), or the error message. */
  const post = async (path: string, body: object): Promise<Account | string> => {
    setBusy(true)
    setError('')
    try {
      const res = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(await detail(res))
      const out: Account = await res.json()
      if (out.signed_in) onSignedIn(out)
      return out
    } catch (err: any) {
      const message = err.message || 'Could not reach the Presentia engine.'
      setError(message)
      return message
    } finally {
      setBusy(false)
    }
  }

  const submit = async () => {
    if (mode === 'sign-in') {
      const out = await post('/api/account/sign-in', { email, password })
      // Signed up but never entered the code: finish that first.
      if (typeof out === 'string' && /not confirmed/i.test(out)) go('confirm', 'Enter the code we emailed when you created the account.')
    } else if (mode === 'sign-up') {
      const out = await post('/api/account/sign-up', { email, password })
      if (typeof out !== 'string' && out.needs_code) {
        // Supabase answers the same for an email that already has an account (and sends nothing).
        emailed(); go('confirm', `We emailed a code to ${email}. If you already have an account, sign in instead.`)
      }
    } else if (mode === 'confirm') {
      await post('/api/account/confirm', { email, code })
    } else if (mode === 'forgot') {
      if (typeof await post('/api/account/forgot', { email }) !== 'string') {
        emailed(); go('reset', `If ${email} has an account, we emailed it a code.`)
      }
    } else {
      await post('/api/account/reset', { email, code, password })
    }
  }

  const resend = async () => {
    const out = await post(mode === 'reset' ? '/api/account/forgot' : '/api/account/resend', { email })
    if (typeof out !== 'string') { emailed(); setNote(`We emailed a new code to ${email}.`) }
  }

  const needsPassword = mode === 'sign-in' || mode === 'sign-up' || mode === 'reset'
  const needsCode = mode === 'confirm' || mode === 'reset'
  const needsConsent = mode === 'sign-in' || mode === 'sign-up'
  const ready = email.includes('@')
    && (!needsPassword || password.length >= (mode === 'sign-in' ? 1 : PASSWORD_MIN))
    && (!needsCode || code.length >= 6)
    && (!needsConsent || agreed)
  const action = {
    'sign-in': busy ? 'Signing in…' : 'Sign in',
    'sign-up': busy ? 'Creating…' : 'Create account',
    confirm: busy ? 'Checking…' : 'Confirm',
    forgot: busy ? 'Sending…' : 'Email me a code',
    reset: busy ? 'Saving…' : 'Set new password',
  }[mode]

  return (
    <div className="settings-section">
      {needsConsent && (
        <>
          <div className="settings-warn">
            <span>
              Signing in copies your classes, students <strong>including their face data</strong>, attendance
              and waiting registrations to your Presentia account online, so the phone app can use them.
              Signing out later stops the copying. Deleting a class or student here also deletes it there.
            </span>
          </div>
          <label className="settings-row-sub" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
            I understand, and my school allows it.
          </label>
        </>
      )}

      {note && <div className="settings-msg" role="status">{note}</div>}

      <label className="field-label" htmlFor="account-email" style={{ margin: 0 }}>Email</label>
      <input id="account-email" className="input" type="email" value={email} autoComplete="email"
             placeholder="you@school.edu.ph" disabled={mode === 'confirm' || mode === 'reset'}
             onChange={(e) => setEmail(e.target.value.trim())} />

      {needsCode && (
        <>
          <label className="field-label" htmlFor="account-code" style={{ margin: 0 }}>
            Code from the email (or paste its link)
          </label>
          <input id="account-code" className="input mono" value={code} autoComplete="one-time-code" autoFocus
                 onChange={(e) => setCode(e.target.value.trim())} />
        </>
      )}

      {needsPassword && (
        <>
          <label className="field-label" htmlFor="account-password" style={{ margin: 0 }}>
            {mode === 'reset' ? 'New password' : 'Password'}
          </label>
          <input id="account-password" className="input" type="password" value={password}
                 autoComplete={mode === 'sign-in' ? 'current-password' : 'new-password'}
                 onChange={(e) => setPassword(e.target.value)}
                 onKeyDown={(e) => { if (e.key === 'Enter' && ready && !busy) submit() }} />
          {mode !== 'sign-in' && (
            <span className="settings-row-sub" style={{ margin: 0 }}>At least {PASSWORD_MIN} characters.</span>
          )}
        </>
      )}

      {error && <div className="settings-msg settings-msg-error" role="alert">{error}</div>}

      <div className="diag-actions">
        {mode === 'sign-in' && <>
          <button className="btn-ghost" disabled={busy} onClick={() => go('forgot')}>Forgot password?</button>
          <button className="btn-ghost" disabled={busy} onClick={() => go('sign-up')}>Create an account</button>
        </>}
        {mode === 'sign-up' && (
          <button className="btn-ghost" disabled={busy} onClick={() => go('sign-in')}>I have an account</button>
        )}
        {(mode === 'confirm' || mode === 'reset') && (
          <button className="btn-ghost" disabled={busy || wait > 0} onClick={resend}>
            {wait > 0 ? `Send a new code (${wait} s)` : 'Send a new code'}
          </button>
        )}
        {(mode === 'forgot' || mode === 'confirm') && (
          <button className="btn-ghost" disabled={busy} onClick={() => go('sign-in')}>Back to sign in</button>
        )}
        <button className="btn-primary" disabled={busy || !ready} onClick={submit}>{action}</button>
      </div>
    </div>
  )
}
