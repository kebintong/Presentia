import { useCallback, useEffect, useState } from 'react'
import { API } from '../api'
import AccountForm, { Account } from './AccountForm'

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

  const post = async (path: string) => {
    setBusy(true)
    setError('')
    try {
      const res = await fetch(`${API}${path}`, { method: 'POST' })
      if (!res.ok) throw new Error(await detail(res))
      setAccount(await res.json())
    } catch (err: any) {
      setError(err.message || 'Could not reach the Presentia engine.')
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
      <AccountForm onSignedIn={setAccount} />
    </div>
  )
}
