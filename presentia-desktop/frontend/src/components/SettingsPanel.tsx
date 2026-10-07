import React, { useState, useEffect } from 'react'
import { EventsOn, EventsOff } from '../../wailsjs/runtime/runtime'
import ThemePicker from './ThemePicker'
import { ThemeKey } from '../themes'
import { applyHideFromCapture, loadHideFromCapture } from '../captureVisibility'
import PerformanceSettings from './PerformanceSettings'
import AccessibilitySettings from './AccessibilitySettings'
import DiagnosticsSettings from './DiagnosticsSettings'
import DataSettings from './DataSettings'
import ReleaseNotes from './ReleaseNotes'
import UpdateHistory, { ReleaseNote } from './UpdateHistory'

export interface UpdateInfo {
  available: boolean
  current: string
  latest: string
  notes: string
  url: string
  checkedAt: string
  /** Every release newer than this computer's version, newest first. */
  releases?: ReleaseNote[]
}

type Phase = 'idle' | 'checking' | 'downloading' | 'ready' | 'installing' | 'error'
export type Tab = 'appearance' | 'performance' | 'accessibility' | 'updates' | 'data' | 'diagnostics'

const TAB_KEY = 'presentia.settingsTab'
const TABS: { id: Tab; label: string }[] = [
  { id: 'appearance', label: 'Appearance' },
  { id: 'performance', label: 'Performance' },
  { id: 'accessibility', label: 'Accessibility' },
  { id: 'updates', label: 'Updates' },
  { id: 'data', label: 'Data' },
  { id: 'diagnostics', label: 'Diagnostics' },
]

function loadTab(): Tab {
  try {
    const t = localStorage.getItem(TAB_KEY)
    if (TABS.some((x) => x.id === t)) return t as Tab
  } catch { /* storage unavailable */ }
  return 'appearance'
}

interface SettingsPanelProps {
  open: boolean
  onClose: () => void
  update: UpdateInfo | null
  version: string
  themeKey: ThemeKey
  onChooseTheme: (key: ThemeKey) => void
  animations: boolean
  onToggleAnimations: () => void
  onRecheck: () => Promise<UpdateInfo | null>
  /** Open on this tab (e.g. Updates, from the "update available" notice). */
  focusTab?: Tab | null
}

const goApp = () => (window as any)['go']?.['main']?.['App']

export default function SettingsPanel({
  open, onClose, update, version, themeKey, onChooseTheme,
  animations, onToggleAnimations, onRecheck, focusTab,
}: SettingsPanelProps) {
  const [phase, setPhase]         = useState<Phase>('idle')
  const [progress, setProgress]   = useState(0)
  const [message, setMessage]     = useState('')
  const [installer, setInstaller] = useState('')
  const [tab, setTab]             = useState<Tab>(loadTab)
  const [hideCapture, setHideCapture] = useState<boolean>(loadHideFromCapture)

  const chooseTab = (t: Tab) => {
    setTab(t)
    try { localStorage.setItem(TAB_KEY, t) } catch { /* ignore */ }
  }

  useEffect(() => {
    if (open && focusTab) setTab(focusTab)
  }, [open, focusTab])

  // Download progress is pushed from Go rather than polled.
  useEffect(() => {
    if (!open) return
    EventsOn('update:progress', (pct: number) => {
      setProgress(typeof pct === 'number' && pct >= 0 ? pct : 0)
    })
    return () => EventsOff('update:progress')
  }, [open])

  if (!open) return null

  const missed = update?.releases?.length ?? 0

  const recheck = async () => {
    setPhase('checking')
    setMessage('')
    try {
      const info = await onRecheck()
      setPhase('idle')
      if (!info?.available) setMessage('You are on the latest version.')
    } catch {
      setPhase('error')
      setMessage('Could not reach GitHub. Check your connection and try again.')
    }
  }

  const download = async () => {
    if (!update?.url) return
    setPhase('downloading')
    setProgress(0)
    setMessage('')
    try {
      const path = await goApp()?.['DownloadUpdate'](update.url)
      setInstaller(path)
      setPhase('ready')
    } catch (err: any) {
      setPhase('error')
      setMessage(String(err?.message || err || 'Download failed.'))
    }
  }

  const install = async () => {
    if (!installer) return
    setPhase('installing')
    setMessage('Presentia will close while it updates, then reopen by itself.')
    try {
      await goApp()?.['InstallUpdate'](installer)
    } catch (err: any) {
      setPhase('error')
      setMessage(String(err?.message || err || 'Could not start the installer.'))
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="card-header">
          <div className="card-header-left">
            <div className="card-icon-badge">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </div>
            <span className="card-title-text">Settings</span>
          </div>
          <button className="btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={onClose}>✕</button>
        </div>

        {/* Each group of settings on its own tab */}
        <div className="settings-tabs" role="tablist" aria-label="Settings sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`settings-tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => chooseTab(t.id)}
            >
              {t.label}
              {t.id === 'updates' && update?.available && <span className="settings-tab-dot" aria-label="Update available" />}
            </button>
          ))}
        </div>

        {/* ── Appearance ─────────────────────────────────────────── */}
        {tab === 'appearance' && (
        <div className="settings-section">
          <span className="field-label">Appearance</span>
          <div className="theme-picker-head">
            <div>
              <div className="settings-row-title">Theme</div>
              <div className="settings-row-sub">Pick how Presentia looks. Your choice is saved on this computer.</div>
            </div>
          </div>
          <ThemePicker value={themeKey} onChange={onChooseTheme} />
          <div className="settings-row">
            <div>
              <div className="settings-row-title">Animations</div>
              <div className="settings-row-sub">
                Smooth motion for pages, cards and dialogs. Turn off to keep everything still.
              </div>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={animations}
              aria-label="Animations"
              className={`toggle-switch ${animations ? 'on' : ''}`}
              onClick={onToggleAnimations}
            >
              <span className="toggle-knob" />
            </button>
          </div>
          <div className="settings-row">
            <div>
              <div className="settings-row-title">Hide Presentia from screen recordings</div>
              <div className="settings-row-sub">
                Keeps Presentia, the bubble and Live View out of screenshots, recordings and screen
                sharing. Off: they show like any other app. The monitor never counts faces on
                Presentia's own windows either way.
              </div>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={hideCapture}
              aria-label="Hide Presentia from screen recordings"
              className={`toggle-switch ${hideCapture ? 'on' : ''}`}
              onClick={() => { const v = !hideCapture; setHideCapture(v); applyHideFromCapture(v) }}
            >
              <span className="toggle-knob" />
            </button>
          </div>
        </div>
        )}

        {/* ── Performance ─────────────────────────────────────── */}
        {tab === 'performance' && <PerformanceSettings />}

        {/* ── Accessibility (check-in strictness) ──────────────── */}
        {tab === 'accessibility' && <AccessibilitySettings />}

        {/* ── Software updates ─────────────────────────────────── */}
        {tab === 'data' && <DataSettings />}
        {tab === 'diagnostics' && <DiagnosticsSettings version={version} />}

        {tab === 'updates' && (
        <div className="settings-section">
          <span className="field-label">Updates</span>

          <div className="settings-row">
            <div>
              <div className="settings-row-title">
                Presentia {version || update?.current || ''}
                {update?.available && <span className="update-pill">Update available</span>}
              </div>
              <div className="settings-row-sub">
                {update?.available
                  ? (missed > 1
                      ? `Version ${update.latest} is ready — ${missed} updates since your version. One install brings you up to date.`
                      : `Version ${update.latest} is ready to install`)
                  : 'You are running the latest version'}
              </div>
            </div>
            {!update?.available && (
              <button className="btn-ghost" onClick={recheck} disabled={phase === 'checking'}>
                {phase === 'checking' ? 'Checking…' : 'Check now'}
              </button>
            )}
          </div>

          {update?.available && (
            missed > 0 ? (
              <div className="settings-notes update-history-box">
                <UpdateHistory releases={update.releases!} />
              </div>
            ) : (
              <div className="settings-notes">
                <ReleaseNotes body={update.notes} empty="No details were given for this version." />
              </div>
            )
          )}

          {update?.available && (
            <div className="settings-update-actions">
              {phase === 'idle' || phase === 'checking' || phase === 'error' ? (
                <button className="btn-primary" onClick={download}>Download update</button>
              ) : null}

              {phase === 'downloading' && (
                <div className="progress-wrap">
                  <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${progress}%` }} />
                  </div>
                  <span className="progress-label">Downloading… {progress}%</span>
                </div>
              )}

              {phase === 'ready' && (
                <>
                  <button className="btn-primary" onClick={install}>Restart and install</button>
                  <span className="settings-row-sub">
                    Presentia closes, updates, and reopens automatically.
                  </span>
                </>
              )}

              {phase === 'installing' && (
                <div className="progress-wrap">
                  <span className="spinner" />
                  <span className="progress-label">Installing…</span>
                </div>
              )}
            </div>
          )}

          {message && (
            <div className={`settings-msg ${phase === 'error' ? 'settings-msg-error' : ''}`}>
              {message}
            </div>
          )}
        </div>
        )}

        <div className="settings-footer">
          Attendance records are stored on this computer and are never uploaded.
        </div>
      </div>
    </div>
  )
}
