import { useEffect, useState } from 'react'
import { API } from '../api'
import AccountForm from './AccountForm'
import PresentiaLogo from './PresentiaLogo'
import TermsText, { TERMS_VERSION } from './TermsText'

type Step = 'welcome' | 'terms' | 'phone' | 'account'

/**
 * First start: Welcome → Terms of Use and Privacy → phone app (optional).
 * `terms` mode shows only the terms, for teachers who already used an older
 * Presentia or when the terms changed. The face models keep downloading in
 * the background meanwhile.
 */
export default function Onboarding({ mode, onDone }: { mode: 'full' | 'terms'; onDone: () => void }) {
  const [step, setStep] = useState<Step>(mode === 'full' ? 'welcome' : 'terms')
  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [phoneApp, setPhoneApp] = useState(false)   // this copy can sync with the phone app

  useEffect(() => {
    fetch(`${API}/api/account`).then((r) => r.json()).then((a) => setPhoneApp(!!a.configured)).catch(() => {})
  }, [])

  const accept = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/setup/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: TERMS_VERSION }),
      })
      if (!res.ok) throw new Error()
      if (mode === 'full' && phoneApp) setStep('phone')
      else onDone()
    } catch {
      setError('Could not save your answer. Presentia is still starting; try again in a moment.')
    } finally {
      setBusy(false)
    }
  }

  const steps: Step[] = mode === 'full' ? (phoneApp ? ['welcome', 'terms', 'phone'] : ['welcome', 'terms']) : ['terms']
  const position = Math.min(steps.indexOf(step === 'account' ? 'phone' : step) + 1, steps.length)

  return (
    <main className="onboarding-page">
      <div className="onboarding-content" aria-label="Welcome to Presentia">
        {steps.length > 1 && (
          <span className="settings-row-sub" style={{ margin: 0, textAlign: step === 'welcome' ? 'center' : undefined }}>
            Step {position} of {steps.length}
          </span>
        )}

        {step === 'welcome' && (
          <div className="settings-section onboarding-welcome">
            <PresentiaLogo height={40} />
            <h2>Welcome to Presentia!</h2>
            <p className="settings-row-sub">
              Take attendance in online and in-person classes. Presentia recognises your students by face and keeps
              your classes on this computer.
            </p>
            <div className="diag-actions">
              <button className="btn-primary" onClick={() => setStep('terms')} autoFocus>Get started</button>
            </div>
          </div>
        )}

        {step === 'terms' && (
          <div className="settings-section">
            <h2>Terms of Use and Privacy</h2>
            {mode === 'terms' && (
              <p className="settings-row-sub">Please read and accept them to keep using Presentia.</p>
            )}
            <div className="terms-box" tabIndex={0}><TermsText /></div>
            <label className="settings-row-sub" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
              I have read and accept the Terms of Use and the Privacy Notice.
            </label>
            {error && <div className="settings-msg settings-msg-error" role="alert">{error}</div>}
            <div className="diag-actions">
              {mode === 'full' && <button className="btn-ghost" onClick={() => setStep('welcome')}>Back</button>}
              <button className="btn-primary" disabled={!agreed || busy} onClick={accept}>
                {busy ? 'Saving…' : 'Accept and continue'}
              </button>
            </div>
          </div>
        )}

        {step === 'phone' && (
          <div className="settings-section">
            <h2>Use the Presentia phone app?</h2>
            <p className="settings-row-sub">
              The phone app takes attendance in person, picks students at random for recitation, and lets you accept
              new registrations from your phone. It needs a free account, and your classes are then copied to it.
            </p>
            <p className="settings-row-sub">
              Not now? Presentia works fully without an account. You can turn it on later in
              Settings → Phone app.
            </p>
            <div className="diag-actions">
              <button className="btn-ghost" onClick={onDone}>Not now</button>
              <button className="btn-primary" onClick={() => setStep('account')} autoFocus>Yes, set it up</button>
            </div>
          </div>
        )}

        {step === 'account' && (
          <div className="settings-section">
            <h2>Your Presentia account</h2>
            <AccountForm onSignedIn={onDone} />
            <div className="diag-actions">
              <button className="btn-ghost" onClick={onDone}>Skip for now</button>
            </div>
          </div>
        )}
      </div>
    </main>
  )
}
