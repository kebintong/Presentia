import React, { useState, useEffect } from 'react'
import TopBar from './components/TopBar'
import Sidebar from './components/Sidebar'
import RegisterPage from './pages/RegisterPage'
import MeetPage from './pages/MeetPage'
import SessionPage from './pages/SessionPage'
import ReportsPage from './pages/ReportsPage'
import './style.css'

type Page = 'register' | 'meet' | 'session' | 'reports'
type Theme = 'dark' | 'light'

const API = 'http://127.0.0.1:7788'

export default function App() {
  const [page, setPage] = useState<Page>('register')
  const [engineReady, setEngineReady] = useState(false)
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('presentia-theme')
    if (saved === 'dark' || saved === 'light') return saved
    return 'dark' // default dark mode
  })

  // Sync theme with DOM and localStorage
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('presentia-theme', theme)
  }, [theme])

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }

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
            return
          }
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

  const PAGE_COMPONENTS: Record<Page, React.ReactNode> = {
    register: <RegisterPage />,
    meet:     <MeetPage />,
    session:  <SessionPage />,
    reports:  <ReportsPage />,
  }

  return (
    <>
      {/* Liquid glass gradient orbs background */}
      <div className="bg-mesh" aria-hidden="true">
        <div className="bg-orb-1" />
        <div className="bg-orb-2" />
        <div className="bg-orb-3" />
        <div className="bg-orb-4" />
      </div>

      {/* Main App Shell */}
      <div className="app-shell">
        {/* Top Header Bar with Theme Switcher */}
        <TopBar
          activePage={page}
          onNavigate={setPage}
          engineReady={engineReady}
          theme={theme}
          onToggleTheme={toggleTheme}
        />

        {/* App Body: Slim icon sidebar + Main viewport */}
        <div className="app-body">
          <Sidebar
            active={page}
            onNavigate={setPage}
            engineReady={engineReady}
          />
          <main className="main-viewport">
            {PAGE_COMPONENTS[page]}
          </main>
        </div>
      </div>
    </>
  )
}
