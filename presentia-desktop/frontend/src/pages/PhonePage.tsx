import PhoneSettings from '../components/PhoneSettings'

/**
 * The phone app (sidebar → phone icon): what it does, and signing in so it
 * shares this computer's classes. Not tied to a class, so it opens from the
 * start screen too.
 */
export default function PhonePage() {
  return (
    <>
      <section className="hero-banner">
        <div className="hero-top-row">
          <div className="hero-title-group">
            <h1 className="hero-title">Phone App</h1>
            <p className="hero-subtitle">Your classes on your phone, in step with this computer</p>
          </div>
        </div>

        <div className="hero-tip-banner">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
            <rect x="6" y="2" width="12" height="20" rx="2.5" /><path d="M11 18h2" />
          </svg>
          <span>
            The Presentia phone app takes <strong>attendance in person</strong> by face or from the class list,
            picks students at random for <strong>recitation</strong> and lets you mark the answer right or wrong,
            and lets you <strong>accept new registrations</strong> from your phone. It needs a free account;
            Presentia works fully without one, and nothing leaves this computer until you sign in.
          </span>
        </div>
      </section>

      <section className="launcher-card" style={{ maxWidth: 680 }} aria-label="Phone app account">
        <PhoneSettings />
      </section>
    </>
  )
}
