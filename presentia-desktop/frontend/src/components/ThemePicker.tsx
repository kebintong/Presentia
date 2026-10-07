import React from 'react'
import { THEMES, ThemeKey } from '../themes'

/*
 * Settings → Appearance: six theme cards in a 2 × 3 grid, each with a small
 * drawing of the app in that theme (like VS Code's start-up theme picker).
 * The drawings are plain CSS (`.theme-mini[data-mini=…]` in style.css), so
 * they stay sharp and need no image files.
 */

interface ThemePickerProps {
  value: ThemeKey
  onChange: (key: ThemeKey) => void
}

export default function ThemePicker({ value, onChange }: ThemePickerProps) {
  // Arrow keys move between cards, as in any radio group.
  const onKeyDown = (e: React.KeyboardEvent, i: number) => {
    const step = { ArrowRight: 1, ArrowDown: 3, ArrowLeft: -1, ArrowUp: -3 }[e.key]
    if (!step) return
    e.preventDefault()
    const next = THEMES[(i + step + THEMES.length) % THEMES.length]
    onChange(next.key)
    const el = (e.currentTarget.parentElement?.querySelector(`[data-key="${next.key}"]`) as HTMLElement | null)
    el?.focus()
  }

  return (
    <div className="theme-grid" role="radiogroup" aria-label="Theme">
      {THEMES.map((t, i) => {
        const selected = t.key === value
        return (
          <button
            key={t.key}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            data-key={t.key}
            className={`theme-card ${selected ? 'selected' : ''}`}
            onClick={() => onChange(t.key)}
            onKeyDown={(e) => onKeyDown(e, i)}
          >
            <span className="theme-mini" data-mini={t.key} aria-hidden="true">
              <span className="tm-top"><span className="tm-logo" /><span className="tm-line" /></span>
              <span className="tm-body">
                <span className="tm-side"><span className="tm-dot" /><span className="tm-nav" /><span className="tm-dot" /></span>
                <span className="tm-main">
                  <span className="tm-title" />
                  <span className="tm-tiles"><span className="tm-tile" /><span className="tm-tile" /><span className="tm-tile" /></span>
                  <span className="tm-card">
                    <span className="tm-text" /><span className="tm-text short" />
                    <span className="tm-btn" />
                  </span>
                </span>
              </span>
            </span>
            {selected && (
              <span className="theme-check">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 12l5 5 9-10" />
                </svg>
              </span>
            )}
            <span className="theme-card-label">
              <span className="theme-card-name">{t.name}</span>
              <span className="theme-card-mode">{t.mode === 'light' ? 'Light' : 'Dark'}</span>
              {t.tag && <span className={`theme-card-tag ${t.tag.toLowerCase()}`}>{t.tag}</span>}
            </span>
          </button>
        )
      })}
    </div>
  )
}
