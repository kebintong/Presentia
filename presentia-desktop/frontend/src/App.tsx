import React, { useState, useEffect } from 'react'
import TopBar from './components/TopBar'
import Sidebar from './components/Sidebar'
import SettingsPanel, { UpdateInfo } from './components/SettingsPanel'
import RegisterPage from './pages/RegisterPage'
import MeetPage from './pages/MeetPage'
import SessionPage from './pages/SessionPage'
import ReportsPage from './pages/ReportsPage'
import './style.css'

type Page = 'register' | 'meet' | 'session' | 'reports'
type Theme = 'dark' | 'light'

const API = 'http://127.0.0.1:7788'

/** Wails bindings, absent when the UI is opened in a plain browser. */
const goApp = () => (window as any)['go']?.['main']?.['App']

export default function App() {
  const [page, setPage] = useState<Page>('register')
  const [engineReady, setEngineReady] = useState(false)
  // What first launch is doing (hardware check, model download progress).
  const [engineMessage, setEngineMessage] = useState('')
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('presentia-theme')
    if (saved === 'dark' || saved === 'light') return saved
    return 'dark' // default dark mode
  })

  // Experimental iridescent design. It is a dark-only look: while it is on
  // the app is forced dark, and the user's own theme choice is kept so that
  // turning it off puts them back where they were.
  const [iridescent, setIridescent] = useState<boolean>(
    () => localStorage.getItem('presentia-iridescent') === '1'
  )
  const effectiveTheme: Theme = iridescent ? 'dark' : theme

  // Sync theme with DOM and localStorage
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', effectiveTheme)
    localStorage.setItem('presentia-theme', theme)
    // The floating bubble is a native Win32 window, so it cannot read the
    // stylesheet — push the theme down to it explicitly.
    goApp()?.['SetBubbleTheme']?.(effectiveTheme === 'dark')
  }, [theme, effectiveTheme])

  const toggleTheme = () => {
    if (iridescent) return // locked to dark
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }

  useEffect(() => {
    const root = document.documentElement
    if (iridescent) root.setAttribute('data-style', 'iridescent')
    else root.removeAttribute('data-style')
    localStorage.setItem('presentia-iridescent', iridescent ? '1' : '0')
    // Swaps the native bubble between the cyan mark and the spectrum mark.
    goApp()?.['SetBubbleStyle']?.(iridescent)
  }, [iridescent])

  // Poll engine status until ready
  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      while (!cancelled) {
        try {
          const res = await fetch(`${API}/api/engine/status`)
          const data = await res.json()
          if (data.ready) {
            setEngineReady(true)
            setEngineMessage('')
            return
          }
          setEngineMessage(data.message || '')
        } catch {
          // sidecar still starting
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
    }
    poll()
    return () => {
      cancelled = true
    }
  }, [])

  // Version + update check. Both are desktop-only and entirely optional: if
  // the bindings are missing or GitHub is unreachable, the app just carries on
  // without a banner.
  const [version, setVersion] = useState('')
  const [update, setUpdate] = useState<UpdateInfo | null>(null)

  useEffect(() => {
    const app = goApp()
    if (!app) return

    app['GetAppVersion']?.()
      .then((v: string) => setVersion(v))
      .catch(() => {})

    // Delayed so the check never competes with sidecar startup.
    const timer = setTimeout(() => {
      app['CheckForUpdate']?.(false)
        .then((info: UpdateInfo) => {
          if (info?.available) setUpdate(info)
        })
        .catch(() => {
          // Offline or rate-limited — not something to bother the user with.
        })
    }, 4000)
    return () => clearTimeout(timer)
  }, [])

  const [settingsOpen, setSettingsOpen] = useState(false)

  // Manual re-check from the Settings panel, bypassing the daily throttle.
  const recheckUpdate = async (): Promise<UpdateInfo | null> => {
    const app = goApp()
    if (!app) return null
    const info: UpdateInfo = await app['CheckForUpdate'](true)
    setUpdate(info?.available ? info : null)
    return info
  }

  const PAGE_COMPONENTS: Record<Page, React.ReactNode> = {
    register: <RegisterPage />,
    meet:     <MeetPage />,
    session:  <SessionPage />,
    reports:  <ReportsPage />,
  }

  return (
    <>
      {/* Main App Shell — flat solid surfaces, no ambient background layer */}
      <div className="app-shell">
        {/* Top Header Bar with Theme Switcher */}
        <TopBar
          activePage={page}
          onNavigate={setPage}
          engineReady={engineReady}
          engineMessage={engineMessage}
          theme={effectiveTheme}
          onToggleTheme={toggleTheme}
          version={version}
          iridescent={iridescent}
        />

        {/* App Body: Slim icon sidebar + Main viewport */}
        <div className="app-body">
          <Sidebar
            active={page}
            onNavigate={setPage}
            engineReady={engineReady}
            onOpenSettings={() => setSettingsOpen(true)}
            updateAvailable={!!update?.available}
          />
          <main className="main-viewport">
            {PAGE_COMPONENTS[page]}
          </main>
        </div>
      </div>

      <SettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        update={update}
        version={version}
        theme={effectiveTheme}
        onToggleTheme={toggleTheme}
        iridescent={iridescent}
        onToggleIridescent={() => setIridescent((v) => !v)}
        onRecheck={recheckUpdate}
      />
    </>
  )
}
