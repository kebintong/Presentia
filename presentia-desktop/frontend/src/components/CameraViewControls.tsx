import { useEffect, useState } from 'react'

/**
 * Webcam orientation: `mirror` is the selfie view (horizontal flip, on by
 * default); `invert` turns the picture upside down (180°) for cameras that
 * are mounted or report upside down. The sidecar applies both to the frames
 * themselves, so face analysis sees what the user sees, and it keeps
 * "turn LEFT / RIGHT" prompts meaning the person's own left and right.
 */
export interface CameraView {
  mirror: boolean
  invert: boolean
}

const KEY = 'presentia.cameraView'
const DEFAULT_VIEW: CameraView = { mirror: true, invert: false }

function loadView(): CameraView {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || 'null')
    if (v && typeof v.mirror === 'boolean' && typeof v.invert === 'boolean') {
      return { mirror: v.mirror, invert: v.invert }
    }
  } catch {
    /* storage unavailable — use the default */
  }
  return DEFAULT_VIEW
}

/** Orientation shared by every page that shows the webcam, remembered between launches. */
export function useCameraView() {
  const [view, setView] = useState<CameraView>(loadView)
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(view))
    } catch {
      /* ignore */
    }
  }, [view])
  return [view, setView] as const
}

interface Props {
  view: CameraView
  onChange: (view: CameraView) => void
}

/** Two toggle buttons for a card header: Mirror and Invert. */
export default function CameraViewControls({ view, onChange }: Props) {
  return (
    <>
      <button
        className={`btn-icon ${view.mirror ? 'btn-icon-active' : ''}`}
        onClick={() => onChange({ ...view, mirror: !view.mirror })}
        title={view.mirror ? 'Mirror: on (selfie view)' : 'Mirror: off'}
        aria-label="Mirror camera"
        aria-pressed={view.mirror}
      >
        {/* flip horizontal */}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3v18" strokeDasharray="2 2.5" />
          <path d="M9 7L3 17h6z" />
          <path d="M15 7l6 10h-6z" />
        </svg>
      </button>
      <button
        className={`btn-icon ${view.invert ? 'btn-icon-active' : ''}`}
        onClick={() => onChange({ ...view, invert: !view.invert })}
        title={view.invert ? 'Invert: on (turned upside down)' : 'Invert: turn the camera upside down'}
        aria-label="Invert camera"
        aria-pressed={view.invert}
      >
        {/* flip vertical */}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12h18" strokeDasharray="2 2.5" />
          <path d="M7 9L17 3v6z" />
          <path d="M7 15l10 6v-6z" />
        </svg>
      </button>
    </>
  )
}
