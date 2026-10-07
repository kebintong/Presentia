import React from 'react'
import BrandTile from './BrandTile'
import { ClassInfo, classLabel } from '../classes'

type Page = 'register' | 'students' | 'meet' | 'session' | 'reports'

interface TopBarProps {
  activePage: Page
  /** Kept for callers; the top bar no longer has back/forward arrows. */
  onNavigate?: (page: Page) => void
  engineReady: boolean
  /** Startup detail while the engine is not ready yet (e.g. download progress). */
  engineMessage?: string
  /** Build version, shown beside the title. Empty outside the desktop shell. */
  version?: string
  /** The open class; null on the start screen. */
  activeClass?: ClassInfo | null
  /** Back to the start screen to pick another class. */
  onSwitchClass?: () => void
}

export default function TopBar({
  activePage,
  engineReady,
  engineMessage = '',
  version,
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
        {/* The app icon on a tile; its colours follow the theme (themes.css). */}
        <div className="top-brand-emblem" title="Presentia">
          <BrandTile />
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

      {/* Right side: Engine Status + Wider Window Controls */}
      <div className="top-bar-right">
        {/* Engine status indicator pill */}
        <div className={`engine-pill ${engineReady ? 'ready' : 'loading'}`}>
          <span className="engine-dot" />
          <span title={engineMessage || undefined}>
            {engineReady ? 'Engine Ready' : engineMessage || 'Starting engine…'}
          </span>
        </div>


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
