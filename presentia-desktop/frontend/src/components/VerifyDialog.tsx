import React, { useState } from 'react'
import Modal from './Modal'
import { ClipboardSetText } from '../../wailsjs/runtime/runtime'

export interface VerifyStudent {
  id: number
  name: string
  state: 'present' | 'unclear' | 'unseen' | 'cam_off' | 'missing' | 'waiting'
  verified?: boolean
  /** Their video has barely changed for a while (photo / frozen feed?). */
  suspect?: boolean
  /** A liveness check is running on them. */
  checking?: boolean
}

export interface CheckState {
  phase: 'choose' | 'starting' | 'running' | 'done'
  student: VerifyStudent
  instructions: string[]
  chatText: string
  prompt: string
  /** 1-based, including the opening "look at the camera" step. */
  step: number
  steps: number
  faceFound: boolean
  smallFace: boolean
  secondsLeft: number
  result: 'passed' | 'failed' | null
  error: string
}

export function newCheck(student: VerifyStudent): CheckState {
  return {
    phase: 'choose', student, instructions: [], chatText: '', prompt: '',
    step: 0, steps: 0, faceFound: false, smallFace: false, secondsLeft: 0,
    result: null, error: '',
  }
}

interface VerifyDialogProps {
  check: CheckState
  onStart: () => void
  onQuickCheck: () => void
  onClose: () => void
}

/** Verify one student from the Monitor roster: a random liveness check
 *  (the instructor sends the steps, Presentia watches the tile) or a quick
 *  face re-check. */
export default function VerifyDialog({ check, onStart, onQuickCheck, onClose }: VerifyDialogProps) {
  const { student, phase } = check
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      const ok = (window as any)['runtime']
        ? await ClipboardSetText(check.chatText)
        : (await navigator.clipboard.writeText(check.chatText), true)
      setCopied(!!ok)
    } catch {
      setCopied(false)
    }
    setTimeout(() => setCopied(false), 2000)
  }

  // On camera (even with the face not clear) is enough to ask for the actions.
  const canStart = student.state === 'present' || student.state === 'unclear'

  return (
    <Modal title={`Verify ${student.name}`} onClose={onClose} width={500}>
      {phase === 'choose' && (
        <div className="check-body">
          {student.suspect && (
            <div className="check-note warn">
              Their video has barely changed for a while. It may be a photo or a frozen feed.
            </div>
          )}
          <p>
            A <strong>liveness check</strong> asks {student.name} to do four quick actions on camera
            (blink, turn their head, look up) in a random order that a recording cannot know. You send
            them the steps; Presentia watches their tile.
          </p>
          <p className="check-muted">
            Tip: pin or spotlight {student.name} in the meeting so their video is large. Blinks are
            hard to see on a small tile.
          </p>
          {!canStart && (
            <div className="check-note">
              {student.name} is not visible right now. They need to be on screen for either check.
            </div>
          )}
          {check.error && <div className="settings-msg settings-msg-error">{check.error}</div>}
          <div className="settings-footer check-actions">
            <button className="btn-ghost" onClick={onQuickCheck} disabled={!canStart}
              title="Compare the face on screen with the registered face again (no actions needed)">
              Quick face re-check
            </button>
            <button className="btn-primary" onClick={onStart} disabled={!canStart}>
              Start liveness check
            </button>
          </div>
        </div>
      )}

      {phase === 'starting' && (
        <div className="check-body" style={{ alignItems: 'center', padding: '24px 0' }}>
          <div className="spinner" />
          <p className="check-muted">Preparing the check…</p>
        </div>
      )}

      {phase === 'running' && (
        <div className="check-body">
          <div>
            <span className="field-label">Send this to {student.name}</span>
            <ol className="check-steps">
              <li className={check.step === 1 ? 'current' : check.step > 1 ? 'done' : ''}>Look at your camera</li>
              {check.instructions.map((text, i) => {
                const n = i + 2 // step numbers include the opening "look at camera"
                return (
                  <li key={i} className={check.step === n ? 'current' : check.step > n ? 'done' : ''}>{text}</li>
                )
              })}
            </ol>
            <button className="btn-ghost check-copy" onClick={copy}>
              {copied ? 'Copied — paste it in the meeting chat' : 'Copy for meeting chat'}
            </button>
          </div>

          <div className="check-live" aria-live="polite">
            <div className="check-live-prompt">{check.prompt || 'Waiting for the first frame…'}</div>
            <div className="check-live-meta">
              <span className={check.faceFound ? 'ok' : 'bad'}>
                {check.faceFound ? 'Face found' : 'Face not on screen'}
              </span>
              <span>Step {Math.max(1, check.step)} of {check.steps || check.instructions.length + 1}</span>
              <span>{check.secondsLeft}s left</span>
            </div>
            {check.smallFace && (
              <div className="check-note warn">
                Their video is small. Pin or spotlight them so blinks can be seen.
              </div>
            )}
          </div>

          <div className="settings-footer check-actions">
            <button className="btn-ghost" onClick={onClose}>Cancel check</button>
          </div>
        </div>
      )}

      {phase === 'done' && (
        <div className="check-body">
          <div className={`check-result ${check.result === 'passed' ? 'pass' : 'fail'}`} role="status">
            <span className="check-result-icon" aria-hidden="true">
              {check.result === 'passed' ? (
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
              ) : (
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
              )}
            </span>
            <div>
              <div className="check-result-title">{check.result === 'passed' ? 'Passed' : 'Not passed'}</div>
              <div className="check-result-text">{check.prompt}</div>
            </div>
          </div>
          {check.result === 'failed' && (
            <p className="check-muted">
              This is logged in the session report. A bad connection or a tiny tile can also cause a
              fail, so you can run the check again.
            </p>
          )}
          <div className="settings-footer check-actions">
            <button className="btn-ghost" onClick={onStart} disabled={!canStart}>Run again</button>
            <button className="btn-primary" onClick={onClose}>Close</button>
          </div>
        </div>
      )}
    </Modal>
  )
}
