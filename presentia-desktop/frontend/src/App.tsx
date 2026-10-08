import React, { useState, useEffect, useLayoutEffect, useRef } from 'react'
import TopBar from './components/TopBar'
import { switchTheme } from './themeTransition'
import { ThemeKey, loadTheme, saveTheme, themeInfo } from './themes'
import { applyHideFromCapture, loadHideFromCapture } from './captureVisibility'
import { listenToBubble, clearPendingBubbleCmds, rememberSource, tellBubbleSource } from './bubbleBridge'
import Sidebar from './components/Sidebar'
import SettingsPanel, { UpdateInfo, Tab as SettingsTab } from './components/SettingsPanel'
import type { ReleaseNote } from './components/UpdateHistory'
import UpdateNotice from './components/UpdateNotice'
import ProblemPrompt from './components/ProblemPrompt'
import RegisterPage from './pages/RegisterPage'
import MeetPage from './pages/MeetPage'
import SessionPage from './pages/SessionPage'
import ReportsPage from './pages/ReportsPage'
import ClassPickerPage from './pages/ClassPickerPage'
import StudentsPage from './pages/StudentsPage'
import { ClassInfo, classLabel } from './classes'
import './style.css'
import './themes.css'

type Page = 'register' | 'students' | 'meet' | 'session' | 'reports'

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

  // The bubble's buttons work from any page: a command arriving while the
  // Monitor page is closed opens it (bubbleBridge.ts).
  const activeClassRef = useRef<ClassInfo | null>(null)
  activeClassRef.current = activeClass
  useEffect(() => listenToBubble(() => {
    if (activeClassRef.current) {
      setPage('meet')
    } else {
      clearPendingBubbleCmds()
      goApp()?.['ShowMainWindow']?.() // pick a class first
    }
  }), [])

  // A different class (or none): the picked area belonged to the old one.
  useEffect(() => {
    rememberSource(null)
    tellBubbleSource('', '', '', activeClass ? classLabel(activeClass) : '')
  }, [activeClass?.id])
  const [engineReady, setEngineReady] = useState(false)
  // What first launch is doing (hardware check, model download progress).
  const [engineMessage, setEngineMessage] = useState('')
  // One of the six looks in Settings → Appearance (see themes.ts).
  const [themeKey, setThemeKey] = useState<ThemeKey>(loadTheme)
  const info = themeInfo(themeKey)

  // Sync theme with DOM and localStorage. A layout effect, so the attributes
  // are set during the commit (the animated theme switch snapshots right after).
  useLayoutEffect(() => {
    const root = document.documentElement
    root.setAttribute('data-theme', info.mode)
    if (info.style) root.setAttribute('data-style', info.style)
    else root.removeAttribute('data-style')
    saveTheme(themeKey)
    // The floating bubble is a native Win32 window, so it cannot read the
    // stylesheet — tell it which of the six looks to draw.
    goApp()?.['SetBubbleTheme']?.(info.mode === 'dark')
    goApp()?.['SetBubbleStyle']?.(themeKey === 'iri')
    goApp()?.['SetBubbleThemeKey']?.(themeKey)
  }, [themeKey, info])

  // Show (default) or hide every Presentia window in screen captures. Applied
  // once at start-up; Settings → Appearance changes it after that.
  useEffect(() => { applyHideFromCapture(loadHideFromCapture()) }, [])

  const chooseTheme = (key: ThemeKey) => {
    if (key === themeKey) return
    switchTheme(() => setThemeKey(key))
  }

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
  // The notes of the version that is running ("What's new" after an update).
  const [installedNotes, setInstalledNotes] = useState<ReleaseNote | null>(null)

  useEffect(() => {
    const app = goApp()
    if (!app) return

    app['GetAppVersion']?.()
      .then((v: string) => setVersion(v))
      .catch(() => {})

    // At start-up (delayed so it never competes with the engine starting),
    // then every hour while the app runs: Presentia often stays open for
    // days in the tray. Go answers from its cache and asks GitHub at most
    // every few hours (update.go).
    const check = () => {
      app['CheckForUpdate']?.(false)
        .then((info: UpdateInfo) => {
          if (info?.available) setUpdate(info)
          if (info?.installed) setInstalledNotes(info.installed)
        })
        .catch(() => {
          // Offline or rate-limited — not something to bother the user with.
        })
    }
    const first = setTimeout(check, 4000)
    const hourly = setInterval(check, 60 * 60 * 1000)
    return () => { clearTimeout(first); clearInterval(hourly) }
  }, [])

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState<SettingsTab | null>(null)
  const openSettings = (tab: SettingsTab | null = null) => {
    setSettingsTab(tab)
    setSettingsOpen(true)
  }

  // Manual re-check from the Settings panel, bypassing the daily throttle.
  const recheckUpdate = async (): Promise<UpdateInfo | null> => {
    const app = goApp()
    if (!app) return null
    const info: UpdateInfo = await app['CheckForUpdate'](true)
    setUpdate(info?.available ? info : null)
    if (info?.installed) setInstalledNotes(info.installed)
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
        {/* Top Header Bar */}
        <TopBar
          activePage={page}
          onNavigate={setPage}
          engineReady={engineReady}
          engineMessage={engineMessage}
          version={version}
          activeClass={activeClass}
          onSwitchClass={switchClass}
        />

        {/* App Body: Slim icon sidebar + Main viewport */}
        <div className="app-body">
          {/* Slow-moving shapes (New Brutalism) or glows (Iridescent) behind the pages; themes.css. */}
          {themeKey === 'brutal' && (
            <div className="theme-deco" aria-hidden="true">
              <span className="deco-ring" />
              <span className="deco-block" />
              <span className="deco-dot" />
              <svg className="deco-squiggle" viewBox="0 0 120 40"><path d="M4 28c12-20 22-20 30 0s20 20 30 0 20-20 30 0 16 14 22 4" /></svg>
            </div>
          )}
          {themeKey === 'iri' && (
            <div className="theme-deco" aria-hidden="true">
              <span className="deco-glow glow-1" />
              <span className="deco-glow glow-2" />
              <span className="deco-glow glow-3" />
            </div>
          )}
          <Sidebar
            active={page}
            onNavigate={setPage}
            engineReady={engineReady}
            onOpenSettings={() => openSettings()}
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
        installed={installedNotes}
        version={version}
        themeKey={themeKey}
        onChooseTheme={chooseTheme}
        animations={animations}
        onToggleAnimations={toggleAnimations}
        onRecheck={recheckUpdate}
        focusTab={settingsTab}
      />

      {/* A newer version is out: say so once, outside monitoring. */}
      <UpdateNotice update={update} hidden={settingsOpen} onOpen={() => openSettings('updates')} />

      {/* Diagnostic mode: offers a report when something fails. */}
      <ProblemPrompt version={version} />
    </>
  )
}
