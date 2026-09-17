import React, { useState } from 'react'

export interface UpdateInfo {
  available: boolean
  current: string
  latest: string
  notes: string
  url: string
  checkedAt: string
}

interface UpdateBannerProps {
  info: UpdateInfo | null
  onDownload: (url: string) => void
}

/**
 * Slim notice shown when a newer release exists on GitHub.
 *
 * Dismissal is remembered per version, so declining 1.1.0 stays quiet until
 * 1.2.0 appears rather than nagging on every launch.
 */
export default function UpdateBanner({ info, onDownload }: UpdateBannerProps) {
  const [dismissed, setDismissed] = useState<string | null>(() => {
    try {
      return localStorage.getItem('presentia-update-dismissed')
    } catch {
      return null
    }
  })

  if (!info || !info.available) return null
  if (dismissed === info.latest) return null

  const dismiss = () => {
    setDismissed(info.latest)
    try {
      localStorage.setItem('presentia-update-dismissed', info.latest)
    } catch {
      // private mode / storage blocked — dismissing for this session is enough
    }
  }

  // Release notes can be long markdown; show the first line only.
  const summary = (info.notes || '').split('\n').find((l) => l.trim().length > 0) || ''

  return (
    <div className="update-banner">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
        <polyline points="7 10 12 15 17 10" />
        <line x1="12" y1="15" x2="12" y2="3" />
      </svg>

      <div className="update-banner-text">
        <strong>Presentia {info.latest} is available</strong>
        <span className="update-banner-sub">
          You have {info.current}
          {summary ? ` · ${summary.slice(0, 90)}` : ''}
        </span>
      </div>

      <button className="btn-primary update-banner-btn" onClick={() => onDownload(info.url)}>
        Download
      </button>
      <button className="btn-ghost update-banner-btn" onClick={dismiss} title="Dismiss until the next version">
        Later
      </button>
    </div>
  )
}
