import { useEffect, useState } from 'react'
import { API } from '../api'
import AccountForm from './AccountForm'
import PresentiaLogo from './PresentiaLogo'
import TermsText, { TERMS_VERSION } from './TermsText'

type Step = 'welcome' | 'terms' | 'phone' | 'account' | 'ready'

export interface EngineState {
  up: boolean                // answering at all
  ready: boolean             // face models loaded
  message: string
  progress: number | null    // model download, 0..1
  error: string
}

// This window's own note of the accepted terms, so the screens can show before
// the engine (which keeps the real record) has started.
const TERMS_KEY = 'presentia.termsAccepted'
/** What to show at start: nothing (accepted), the new terms only, or everything. */
export const firstStartMode = (): 'full' | 'terms' | null => {
  let seen: string | null = null
  try { seen = localStorage.getItem(TERMS_KEY) } catch { /* storage unavailable */ }
  return seen === TERMS_VERSION ? null : seen ? 'terms' : 'full'
}
export const rememberTerms = () => {
  try { localStorage.setItem(TERMS_KEY, TERMS_VERSION) } catch { /* storage unavailable */ }
}

/**
 * First start: Welcome → Terms of Use and Privacy → phone app (optional) →
 * Getting ready (the engine starting and downloading the face models; the
 * teacher continues once it is ready). `terms` mode shows only the terms, for
 * teachers who already used an older Presentia or when the terms changed.
 */
export default function Onboarding({ mode, engine, onDone }: {
  mode: 'full' | 'terms'
  engine: EngineState
  onDone: () => void
}) {
  const [step, setStep] = useState<Step>(mode === 'full' ? 'welcome' : 'terms')
  const [agreed, setAgreed] = useState(false)
  const [phoneApp, setPhoneApp] = useState(true)   // this copy can sync with the phone app
  const [recorded, setRecorded] = useState(false)  // the engine has the acceptance on record

  useEffect(() => {
    if (engine.up) fetch(`${API}/api/account`).then((r) => r.json()).then((a) => setPhoneApp(!!a.configured)).catch(() => {})
  }, [engine.up])

  // Teachers who only re-accept the terms skip "Getting ready" when the engine already is.
  const finish = () => (mode === 'terms' && engine.ready && recorded ? onDone() : setStep('ready'))

  // Accepting never waits for the engine: the acceptance is sent as soon as it answers.
  const accept = () => {
    rememberTerms()
    const record = async () => {
      for (;;) {
        try {
          const res = await fetch(`${API}/api/setup/accept`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ version: TERMS_VERSION }),
          })
          if (res.ok) return setRecorded(true)
        } catch { /* engine still starting */ }
        await new Promise((r) => setTimeout(r, 1500))
      }
    }
    record()
    if (mode === 'full' && phoneApp) setStep('phone')
    else finish()
  }

  const steps: Step[] = mode === 'full'
    ? (phoneApp ? ['welcome', 'terms', 'phone', 'ready'] : ['welcome', 'terms', 'ready'])
    : ['terms', 'ready']
  const position = Math.min(steps.indexOf(step === 'account' ? 'phone' : step) + 1, steps.length)
  const percent = engine.progress === null ? null : Math.round(engine.progress * 100)

  return (
    <main className="onboarding-page">
      <div className="onboarding-content" aria-label="Welcome to Presentia">
        {steps.length > 1 && (
          <span className="settings-row-sub" style={{ margin: 0, textAlign: step === 'welcome' || step === 'ready' ? 'center' : undefined }}>
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
            <div className="diag-actions">
              {mode === 'full' && <button className="btn-ghost" onClick={() => setStep('welcome')}>Back</button>}
              <button className="btn-primary" disabled={!agreed} onClick={accept}>Accept and continue</button>
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
              <button className="btn-ghost" onClick={finish}>Not now</button>
              <button className="btn-primary" onClick={() => setStep('account')} autoFocus>Yes, set it up</button>
            </div>
          </div>
        )}

        {step === 'account' && (
          <div className="settings-section">
            <h2>Your Presentia account</h2>
            {!engine.up && (
              <div className="settings-msg" role="status">
                Presentia is still starting. Signing in works once it is up, or skip and do it later in
                Settings → Phone app.
              </div>
            )}
            <AccountForm onSignedIn={finish} />
            <div className="diag-actions">
              <button className="btn-ghost" onClick={finish}>Skip for now</button>
            </div>
          </div>
        )}

        {step === 'ready' && (
          <div className="settings-section onboarding-welcome" aria-live="polite">
            {engine.ready
              ? <div className="ready-mark" aria-hidden="true">✓</div>
              : !engine.error && <span className="spinner ready-spinner" aria-hidden="true" />}
            <h2>
              {engine.ready ? 'Presentia is ready' : engine.error ? 'Presentia could not get ready' : 'Getting Presentia ready…'}
            </h2>
            {!engine.ready && !engine.error && (
              <>
                <p className="ready-status">
                  {engine.message || (engine.up ? 'Starting…' : "Starting Presentia's engine…")}
                </p>
                {percent !== null && (
                  <div className="ready-bar" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
                    <span style={{ width: `${percent}%` }} />
                  </div>
                )}
                <p className="settings-row-sub">
                  The first start downloads the face models (about 195 MB) once. This can take a few minutes.
                </p>
              </>
            )}
            {engine.error && (
              <>
                <div className="settings-msg settings-msg-error" role="alert" style={{ textAlign: 'left' }}>{engine.error}</div>
                <p className="settings-row-sub">
                  Check the internet connection, then restart Presentia. Settings → Diagnostics shows more.
                </p>
              </>
            )}
            <div className="diag-actions">
              {engine.error && <button className="btn-ghost" disabled={!recorded} onClick={onDone}>Continue anyway</button>}
              <button className="btn-primary" disabled={!engine.ready || !recorded} onClick={onDone}>Open Presentia</button>
            </div>
          </div>
        )}
      </div>
    </main>
  )
}
