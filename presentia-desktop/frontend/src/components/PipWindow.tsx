import React, { useRef, useState, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'

interface PipWindowProps {
  title: string
  /** Small status text shown beside the title (e.g. "Streaming"). */
  badge?: string
  /** Puts the content back into its card. */
  onReturn: () => void
  children: React.ReactNode
  width?: number
}

const EDGE = 16 // gap kept between the window and the viewport edge

/**
 * A floating picture-in-picture panel that stays on top of the page while the
 * user scrolls or fills in forms. It opens in the bottom-right corner and can
 * be dragged anywhere by its header.
 *
 * Position is stored as an offset from the bottom-right corner, so the panel
 * stays put in that corner when the app window is resized.
 */
export default function PipWindow({ title, badge, onReturn, children, width = 320 }: PipWindowProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [offset, setOffset] = useState({ right: EDGE, bottom: EDGE })
  const drag = useRef<{ x: number; y: number; right: number; bottom: number } | null>(null)

  // Never let the panel end up (partly) off screen.
  const clamp = useCallback((right: number, bottom: number) => {
    const el = panelRef.current
    const w = el?.offsetWidth ?? width
    const h = el?.offsetHeight ?? 200
    return {
      right: Math.min(Math.max(EDGE, right), Math.max(EDGE, window.innerWidth - w - EDGE)),
      bottom: Math.min(Math.max(EDGE, bottom), Math.max(EDGE, window.innerHeight - h - EDGE)),
    }
  }, [width])

  useEffect(() => {
    const onResize = () => setOffset((o) => clamp(o.right, o.bottom))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [clamp])

  const onPointerDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('button')) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, y: e.clientY, ...offset }
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    setOffset(clamp(d.right - (e.clientX - d.x), d.bottom - (e.clientY - d.y)))
  }
  const onPointerUp = (e: React.PointerEvent) => {
    drag.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  return createPortal(
    <div
      ref={panelRef}
      className="pip-window"
      style={{ width, right: offset.right, bottom: offset.bottom }}
      role="dialog"
      aria-label={`${title} (picture-in-picture)`}
    >
      <div
        className="pip-header"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <div className="pip-title">
          <span className="pip-grip" aria-hidden="true" />
          <span>{title}</span>
          {badge && <span className="card-count-pill">{badge}</span>}
        </div>
        <button className="pip-btn" onClick={onReturn} title="Return to card" aria-label="Return to card">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M10 14l-4 4M6 14v4h4" />
          </svg>
        </button>
      </div>
      <div className="pip-body">{children}</div>
    </div>,
    document.body
  )
}
