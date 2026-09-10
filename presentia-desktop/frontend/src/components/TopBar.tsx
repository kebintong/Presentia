import React from 'react'

type Page = 'register' | 'meet' | 'session' | 'reports'

interface TopBarProps {
  activePage: Page
  onNavigate: (page: Page) => void
  engineReady: boolean
  theme: 'dark' | 'light'
  onToggleTheme: () => void
}

const PAGE_ORDER: Page[] = ['register', 'meet', 'session', 'reports']

export default function TopBar({
  activePage,
  onNavigate,
  engineReady,
  theme,
  onToggleTheme,
}: TopBarProps) {
  const currentIndex = PAGE_ORDER.indexOf(activePage)

  const handleBack = () => {
    if (currentIndex > 0) {
      onNavigate(PAGE_ORDER[currentIndex - 1])
    }
  }

  const handleForward = () => {
    if (currentIndex < PAGE_ORDER.length - 1) {
      onNavigate(PAGE_ORDER[currentIndex + 1])
    }
  }

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
    <header className="top-bar" onDoubleClick={handleToggleMaximise}>
      {/* Left side: "P" Brand Logo + Nav arrows + Title */}
      <div className="top-bar-left">
        {/* Bold "P" Logo matching the browser tab */}
        <div className="top-brand-emblem" title="Presentia">
          <span className="brand-p-logo">P</span>
        </div>

        {/* Navigation arrows */}
        <div className="top-nav-arrows">
          <button
            className="top-arrow-btn"
            onClick={handleBack}
            disabled={currentIndex === 0}
            title="Previous section"
            aria-label="Previous"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>
          <button
            className="top-arrow-btn"
            onClick={handleForward}
            disabled={currentIndex === PAGE_ORDER.length - 1}
            title="Next section"
            aria-label="Next"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 18l6-6-6-6" />
            </svg>
          </button>
        </div>

        {/* App Title */}
        <span className="top-app-title">Presentia</span>
        <span className="top-page-tag">
          {activePage === 'register' && 'Student Registration'}
          {activePage === 'meet' && 'Meeting Monitor'}
          {activePage === 'session' && 'Class Session'}
          {activePage === 'reports' && 'Attendance Reports'}
        </span>
      </div>

      {/* Right side: Engine Status + Theme Toggle + Wider Window Controls */}
      <div className="top-bar-right">
        {/* Engine status indicator pill */}
        <div className={`engine-pill ${engineReady ? 'ready' : 'loading'}`}>
          <span className="engine-dot" />
          <span>{engineReady ? 'Engine Ready' : 'Starting Sidecar...'}</span>
        </div>

        {/* Theme Toggle Button (Dark / Light) */}
        <button
          className="theme-toggle-btn"
          onClick={onToggleTheme}
          title={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
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
