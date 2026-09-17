import React, { useState, useEffect } from 'react'
import { EventsOn, EventsOff } from '../../wailsjs/runtime/runtime'

export interface UpdateInfo {
  available: boolean
  current: string
  latest: string
  notes: string
  url: string
  checkedAt: string
}

type Phase = 'idle' | 'checking' | 'downloading' | 'ready' | 'installing' | 'error'

interface SettingsPanelProps {
  open: boolean
  onClose: () => void
  update: UpdateInfo | null
  version: string
  theme: 'dark' | 'light'
  onToggleTheme: () => void
  onRecheck: () => Promise<UpdateInfo | null>
}

const goApp = () => (window as any)['go']?.['main']?.['App']

export default function SettingsPanel({
  open, onClose, update, version, theme, onToggleTheme, onRecheck,
}: SettingsPanelProps) {
  const [phase, setPhase]         = useState<Phase>('idle')
  const [progress, setProgress]   = useState(0)
  const [message, setMessage]     = useState('')
  const [installer, setInstaller] = useState('')

  // Download progress is pushed from Go rather than polled.
  useEffect(() => {
    if (!open) return
    EventsOn('update:progress', (pct: number) => {
      setProgress(typeof pct === 'number' && pct >= 0 ? pct : 0)
    })
    return () => EventsOff('update:progress')
  }, [open])

  if (!open) return null

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

  // Release notes can be long markdown; show the first few lines.
  const notes = (update?.notes || '')
    .split('\n').map((l) => l.trim()).filter(Boolean).slice(0, 6)

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

        {/* ── Appearance ─────────────────────────────────────────── */}
        <div className="settings-section">
          <span className="field-label">Appearance</span>
          <div className="settings-row">
            <div>
              <div className="settings-row-title">Theme</div>
              <div className="settings-row-sub">Currently {theme === 'dark' ? 'dark' : 'light'}</div>
            </div>
            <button className="btn-ghost" onClick={onToggleTheme}>
              Switch to {theme === 'dark' ? 'Light' : 'Dark'}
            </button>
          </div>
        </div>

        {/* ── Updates ────────────────────────────────────────────── */}
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
                  ? `Version ${update.latest} is ready to install`
                  : 'You are running the latest version'}
              </div>
            </div>
            {!update?.available && (
              <button className="btn-ghost" onClick={recheck} disabled={phase === 'checking'}>
                {phase === 'checking' ? 'Checking…' : 'Check now'}
              </button>
            )}
          </div>

          {update?.available && notes.length > 0 && (
            <ul className="settings-notes">
              {notes.map((line, i) => (
                <li key={i}>{line.replace(/^[-*]\s*/, '')}</li>
              ))}
            </ul>
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

        <div className="settings-footer">
          Attendance records are stored on this computer and are never uploaded.
        </div>
      </div>
    </div>
  )
}
