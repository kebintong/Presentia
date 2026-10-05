import { useEffect, useState } from 'react'
import ReportDialog from './ReportDialog'
import {
  Problem, buildReport, copyText, onProblem, reportFileName, saveText,
} from '../diagnostics'

/**
 * Diagnostic mode only: a small box in the corner when something fails,
 * offering to send, copy or save a report. It never blocks the screen.
 */
export default function ProblemPrompt({ version }: { version: string }) {
  const [problems, setProblems] = useState<Problem[]>([])
  const [reporting, setReporting] = useState<Problem | null>(null)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')

  useEffect(() => onProblem((p) => {
    setProblems((list) => [...list, p].slice(-10))
    setNote('')
  }), [])

  const current = problems[problems.length - 1]
  if (!current && !reporting) return null

  const dismiss = () => {
    setProblems((list) => list.slice(0, -1))
    setNote('')
  }

  const copy = async () => {
    setBusy('copy')
    try {
      const ok = await copyText(await buildReport(version, current))
      setNote(ok ? 'Report copied.' : 'Could not copy.')
    } catch {
      setNote('The report could not be prepared.')
    } finally {
      setBusy('')
    }
  }

  const save = async () => {
    setBusy('save')
    try {
      const where = await saveText(reportFileName(), await buildReport(version, current))
      if (where) setNote(`Saved to ${where}.`)
    } catch {
      setNote('The report could not be saved.')
    } finally {
      setBusy('')
    }
  }

  return (
    <>
      {current && (
        <div className="problem-prompt" role="alert" aria-live="assertive">
          <div className="problem-head">
            <span className="problem-icon" aria-hidden="true">!</span>
            <div className="problem-text">
              <div className="problem-title">{current.title} failed</div>
              <div className="problem-message">{current.message}</div>
            </div>
            <button className="problem-close" onClick={dismiss} aria-label="Dismiss">✕</button>
          </div>
          {note && <div className="problem-note" role="status">{note}</div>}
          <div className="problem-actions">
            <span className="problem-count">
              {problems.length > 1 ? `${problems.length - 1} more` : 'Diagnostic mode'}
            </span>
            <button className="btn-ghost" onClick={copy} disabled={!!busy}>{busy === 'copy' ? 'Preparing…' : 'Copy'}</button>
            <button className="btn-ghost" onClick={save} disabled={!!busy}>{busy === 'save' ? 'Preparing…' : 'Save…'}</button>
            <button className="btn-primary" onClick={() => setReporting(current)} disabled={!!busy}>Send report</button>
          </div>
        </div>
      )}
      {reporting && (
        <ReportDialog
          version={version}
          problem={reporting}
          onClose={() => {
            setReporting(null)
            setProblems((list) => list.filter((p) => p.id !== reporting.id))
          }}
        />
      )}
    </>
  )
}
