import React, { useState, useEffect, useLayoutEffect } from 'react'
import TopBar from './components/TopBar'
import { switchTheme } from './themeTransition'
import Sidebar from './components/Sidebar'
import SettingsPanel, { UpdateInfo } from './components/SettingsPanel'
import ProblemPrompt from './components/ProblemPrompt'
import RegisterPage from './pages/RegisterPage'
import MeetPage from './pages/MeetPage'
import SessionPage from './pages/SessionPage'
import ReportsPage from './pages/ReportsPage'
import ClassPickerPage from './pages/ClassPickerPage'
import StudentsPage from './pages/StudentsPage'
import { ClassInfo } from './classes'
import './style.css'

type Page = 'register' | 'students' | 'meet' | 'session' | 'reports'
type Theme = 'dark' | 'light'

const API = 'http://127.0.0.1:7788'

/** Wails bindings, absent when the UI is opened in a plain browser. */
const goApp = () => (window as any)['go']?.['main']?.['App']

export default function App() {
  const [page, setPage] = useState<Page>('register')
  // The class being worked on. The app always opens on the class picker
  // (null), like the Classroom home page; every page then works on this
  // class's roster and sessions only.
  const [activeClass, setActiveClass] = useState<ClassInfo | null>(null)

  const openClass = async (cls: ClassInfo) => {
    // A class with nobody in it starts on Register; otherwise straight to
    // monitoring, which is what an instructor opens a past class for.
    setPage(cls.student_count > 0 ? 'meet' : 'register')
    setActiveClass(cls)
    try {
      const res = await fetch(`${API}/api/classes/${cls.id}/open`, { method: 'POST' })
      if (res.ok) setActiveClass(await res.json())
    } catch {
      // Only the "last opened" ordering is lost; the class still works.
    }
  }

  const switchClass = () => setActiveClass(null)
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

  // Sync theme with DOM and localStorage. A layout effect, so the attribute
  // is set during the commit (the animated theme switch snapshots right after).
  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-theme', effectiveTheme)
    localStorage.setItem('presentia-theme', theme)
    // The floating bubble is a native Win32 window, so it cannot read the
    // stylesheet — push the theme down to it explicitly.
    goApp()?.['SetBubbleTheme']?.(effectiveTheme === 'dark')
  }, [theme, effectiveTheme])

  const toggleTheme = () => {
    if (iridescent) return // locked to dark
    switchTheme(() => setTheme((prev) => (prev === 'dark' ? 'light' : 'dark')))
  }

  useLayoutEffect(() => {
    const root = document.documentElement
    if (iridescent) root.setAttribute('data-style', 'iridescent')
    else root.removeAttribute('data-style')
    localStorage.setItem('presentia-iridescent', iridescent ? '1' : '0')
    // Swaps the native bubble between the cyan mark and the spectrum mark.
    goApp()?.['SetBubbleStyle']?.(iridescent)
  }, [iridescent])

  // Interface motion (page, card and dialog animations). Until the user
  // chooses, follow the system's "reduce motion" preference.
  const [animations, setAnimations] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem('presentia-animations')
      if (saved === 'on') return true
      if (saved === 'off') return false
    } catch { /* storage unavailable */ }
    return !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  })

  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-motion', animations ? 'on' : 'off')
  }, [animations])

  const toggleAnimations = () => {
    setAnimations((v) => {
      try { localStorage.setItem('presentia-animations', v ? 'off' : 'on') } catch { /* ignore */ }
      return !v
    })
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

  // key = class id: switching class remounts the page, so nothing (a
  // running camera, a roster, a selected report) carries over between classes.
  const PAGE_COMPONENTS: Record<Page, React.ReactNode> = activeClass
    ? {
        register: <RegisterPage key={activeClass.id} classInfo={activeClass} onOpenStudents={() => setPage('students')} />,
        students: <StudentsPage key={activeClass.id} classInfo={activeClass} />,
        meet:     <MeetPage key={activeClass.id} classInfo={activeClass} />,
        session:  <SessionPage key={activeClass.id} classInfo={activeClass} />,
        reports:  <ReportsPage key={activeClass.id} classInfo={activeClass} />,
      }
    : { register: null, students: null, meet: null, session: null, reports: null }

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
          activeClass={activeClass}
          onSwitchClass={switchClass}
        />

        {/* App Body: Slim icon sidebar + Main viewport */}
        <div className="app-body">
          <Sidebar
            active={page}
            onNavigate={setPage}
            engineReady={engineReady}
            onOpenSettings={() => setSettingsOpen(true)}
            updateAvailable={!!update?.available}
            showNav={!!activeClass}
          />
          <main className="main-viewport">
            {activeClass
              ? PAGE_COMPONENTS[page]
              : <ClassPickerPage onOpen={openClass} />}
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
        onToggleIridescent={() => switchTheme(() => setIridescent((v) => !v))}
        animations={animations}
        onToggleAnimations={toggleAnimations}
        onRecheck={recheckUpdate}
      />

      {/* Diagnostic mode: offers a report when something fails. */}
      <ProblemPrompt version={version} />
    </>
  )
}
