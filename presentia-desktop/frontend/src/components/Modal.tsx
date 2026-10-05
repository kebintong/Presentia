import React, { useEffect } from 'react'

interface ModalProps {
  title: string
  icon?: React.ReactNode
  onClose: () => void
  children: React.ReactNode
  /** Narrower than the settings panel by default — these are short forms. */
  width?: number
}

/** Small dialog on the same scrim/panel styles as the Settings panel.
 *  Closes on Escape or a click outside the panel. */
export default function Modal({ title, icon, onClose, children, width = 440 }: ModalProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div
        className="settings-panel"
        style={{ maxWidth: width }}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-header">
          <div className="card-header-left">
            {icon && <div className="card-icon-badge">{icon}</div>}
            <span className="card-title-text">{title}</span>
          </div>
          <button
            className="btn-ghost"
            style={{ padding: '4px 10px', fontSize: 12 }}
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
