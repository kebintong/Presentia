import React from 'react'
import PresentiaLogo from './PresentiaLogo'
import markLight from '../assets/images/presentia-mark-light.png'
import markDark from '../assets/images/presentia-mark-dark.png'
import { ClassInfo, classLabel } from '../classes'

type Page = 'register' | 'students' | 'meet' | 'session' | 'reports'

interface TopBarProps {
  activePage: Page
  /** Kept for callers; the top bar no longer has back/forward arrows. */
  onNavigate?: (page: Page) => void
  engineReady: boolean
  /** Startup detail while the engine is not ready yet (e.g. download progress). */
  engineMessage?: string
  theme: 'dark' | 'light'
  onToggleTheme: () => void
  /** Build version, shown beside the title. Empty outside the desktop shell. */
  version?: string
  /** Experimental iridescent design: spectrum mark, theme locked to dark. */
  iridescent?: boolean
  /** The open class; null on the start screen. */
  activeClass?: ClassInfo | null
  /** Back to the start screen to pick another class. */
  onSwitchClass?: () => void
}

/** Brand mark size in the top bar. MARK_WIDEN stretches it horizontally
 *  (1 = the artwork's natural 1325:2000 proportions). */
const MARK_H = 24
const MARK_WIDEN = 1.2
const MARK_W = Math.round(((MARK_H * 1325) / 2000) * MARK_WIDEN)

export default function TopBar({
  activePage,
  engineReady,
  engineMessage = '',
  theme,
  onToggleTheme,
  version,
  iridescent = false,
  activeClass = null,
  onSwitchClass,
}: TopBarProps) {
  // Window control actions (Wails Go binding + Runtime fallback)
  const handleMinimise = () => {
    if ((window as any)['go']?.['main']?.['App']?.['WindowMinimise']) {
      (window as any)['go']['main']['App']['WindowMinimise']()
    } else if ((window as any)['runtime']?.['WindowMinimise']) {
      (window as any)['runtime']['WindowMinimise']()
    }
  }

  const handleToggleMaximise = () => {
    if ((window as any)['go']?.['main']?.['App']?.['WindowToggleMaximise']) {
      (window as any)['go']['main']['App']['WindowToggleMaximise']()
    } else if ((window as any)['runtime']?.['WindowToggleMaximise']) {
      (window as any)['runtime']['WindowToggleMaximise']()
    }
  }

  const handleClose = () => {
    if ((window as any)['go']?.['main']?.['App']?.['WindowClose']) {
      (window as any)['go']['main']['App']['WindowClose']()
    } else if ((window as any)['runtime']?.['Quit']) {
      (window as any)['runtime']['Quit']()
    }
  }

  return (
    // No double-click-to-maximise here: it also fired when buttons on the bar
    // were double-clicked. Maximise with the window button instead.
    <header className="top-bar">
      {/* Left side: "P" Brand Logo + Title */}
      <div className="top-bar-left">
        {/* Presentia mark — cyan in light mode, white in dark, spectrum when iridescent */}
        <div className="top-brand-emblem" title="Presentia">
          {iridescent ? (
            <PresentiaLogo height={MARK_H} width={MARK_W} className="brand-iri-logo" />
          ) : (
            <img
              src={theme === 'dark' ? markDark : markLight}
              alt=""
              className="brand-mark"
              height={MARK_H}
              width={MARK_W}
              style={{ width: MARK_W, height: MARK_H }}
            />
          )}
        </div>

        {/* App Title */}
        <span className="top-app-title">Presentia</span>
        {version && <span className="top-version-tag" title={`Version ${version}`}>v{version}</span>}
        {activeClass && (
          <button
            className="top-class-chip"
            onClick={onSwitchClass}
            title={`${classLabel(activeClass)} — click to switch class`}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M22 10 12 5 2 10l10 5 10-5z" />
              <path d="M6 12v5c3 3 9 3 12 0v-5" />
            </svg>
            <span>{classLabel(activeClass)}</span>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
        )}
        <span className="top-page-tag">
          {!activeClass && 'Your Classes'}
          {activeClass && activePage === 'register' && 'Student Registration'}
          {activeClass && activePage === 'students' && 'Students'}
          {activeClass && activePage === 'meet' && 'Meeting Monitor'}
          {activeClass && activePage === 'session' && 'Class Session'}
          {activeClass && activePage === 'reports' && 'Attendance Reports'}
        </span>
      </div>

      {/* Right side: Engine Status + Theme Toggle + Wider Window Controls */}
      <div className="top-bar-right">
        {/* Engine status indicator pill */}
        <div className={`engine-pill ${engineReady ? 'ready' : 'loading'}`}>
          <span className="engine-dot" />
          <span title={engineMessage || undefined}>
            {engineReady ? 'Engine Ready' : engineMessage || 'Starting engine…'}
          </span>
        </div>

        {/* Theme Toggle Button (Dark / Light) */}
        <button
          className="theme-toggle-btn"
          onClick={onToggleTheme}
          disabled={iridescent}
          title={
            iridescent
              ? 'Iridescent Design is dark-only — turn it off in Settings to use Light mode'
              : theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'
          }
          aria-label="Toggle Theme"
        >
          {theme === 'dark' ? (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
            </svg>
          ) : (
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          )}
          <span>{theme === 'dark' ? 'Light' : 'Dark'}</span>
        </button>

        {/* Wider Window Controls (Minimize, Maximize, Close) */}
        <div className="window-controls">
          <button
            className="win-btn win-min"
            onClick={handleMinimise}
            title="Minimize"
            aria-label="Minimize"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
              <line x1="4" y1="12" x2="20" y2="12" />
            </svg>
          </button>
          <button
            className="win-btn win-max"
            onClick={handleToggleMaximise}
            title="Maximize"
            aria-label="Maximize"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <rect x="4" y="4" width="16" height="16" rx="2" />
            </svg>
          </button>
          <button
            className="win-btn win-close"
            onClick={handleClose}
            title="Close"
            aria-label="Close"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
    </header>
  )
}
