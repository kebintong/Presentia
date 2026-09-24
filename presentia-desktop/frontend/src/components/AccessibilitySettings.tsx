import { useEffect, useState } from 'react'

const API = 'http://127.0.0.1:7788'

interface Checks {
  random_challenges: boolean
  antispoof: boolean
  profile: 'low' | 'balanced' | 'high'
}

/**
 * Settings → Accessibility: how demanding check-in is. Randomised
 * challenges stop pre-recorded clips; replay detection stops photos and
 * phone screens. Either can be turned off for students who need it.
 */
export default function AccessibilitySettings() {
  const [checks, setChecks] = useState<Checks | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetch(`${API}/api/checks`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setChecks)
      .catch(() => setError('These settings are unavailable until the engine has started.'))
  }, [])

  const save = async (patch: Partial<Checks>) => {
    setSaving(true)
    try {
      const res = await fetch(`${API}/api/checks`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) throw new Error()
      setChecks(await res.json())
      setError('')
    } catch {
      setError('Could not save the setting.')
    } finally {
      setSaving(false)
    }
  }

  const toggle = (label: string, on: boolean, onClick: () => void) => (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`toggle-switch ${on ? 'on' : ''}`}
      disabled={!checks || saving}
      onClick={onClick}
    >
      <span className="toggle-knob" />
    </button>
  )

  return (
    <div className="settings-section">
      <span className="field-label">Check-in</span>

      <div className="settings-row">
        <div>
          <div className="settings-row-title">Randomised liveness challenges</div>
          <div className="settings-row-sub">
            Each check-in asks for a different random order of actions (blink, turn left or right,
            look up), each with a time limit, so a recorded video or GIF cannot pass. Turn off for
            students who need more time: they then get the same fixed steps without per-step
            limits.
          </div>
        </div>
        {toggle('Randomised liveness challenges', !!checks?.random_challenges,
          () => save({ random_challenges: !checks?.random_challenges }))}
      </div>

      <div className="settings-row">
        <div>
          <div className="settings-row-title">Photo and screen-replay detection</div>
          <div className="settings-row-sub">
            Flags a face that looks like a printed photo or a phone or monitor held up to the
            camera, and stops that check-in.
          </div>
          <div className="settings-warn">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" style={{ flexShrink: 0, marginTop: 1 }}>
              <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
              <path d="M12 9v4M12 17h.01" />
            </svg>
            <span>
              Performance: runs two extra small models during every check-in (a few MB to
              download the first time). On slower computers check-in may feel less smooth
              {checks?.profile === 'low' ? ' — this computer uses the Low profile' : ''}.
            </span>
          </div>
        </div>
        {toggle('Photo and screen-replay detection', !!checks?.antispoof,
          () => save({ antispoof: !checks?.antispoof }))}
      </div>

      {error && <div className="settings-msg settings-msg-error">{error}</div>}
    </div>
  )
}
