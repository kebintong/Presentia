import { useEffect, useState } from 'react'
import Modal from './Modal'
import {
  Problem, buildReport, copyText, getMode, reportFileName, reportSummary, reportsHost, saveText, sendReport,
} from '../diagnostics'

interface Props {
  version: string
  /** The problem being reported, or null for "Report a problem". */
  problem: Problem | null
  onClose: () => void
}

/**
 * Shows the report before anything leaves the computer, then sends it to the
 * Presentia website, or copies or saves it instead.
 */
export default function ReportDialog({ version, problem, onClose }: Props) {
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState<{ id: string; github: boolean } | null>(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    let alive = true
    buildReport(version, problem)
      .then((t) => alive && setText(t))
      .catch(() => alive && setError('The report could not be prepared: the Presentia engine is not answering.'))
    return () => { alive = false }
  }, [version, problem])

  const send = async () => {
    if (!text) return
    setBusy(true)
    setError('')
    try {
      setSent(await sendReport(reportSummary(problem), text, version))
    } catch (err: any) {
      setError(`${err.message} You can copy or save the report instead.`)
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    if (text && (await copyText(text))) setNote('Report copied. Paste it into a message to whoever looks after Presentia.')
  }

  const save = async () => {
    if (!text) return
    try {
      const where = await saveText(reportFileName(), text)
      if (where) setNote(`Saved to ${where}.`)
    } catch (err: any) {
      setError(`Could not save: ${err?.message || err}`)
    }
  }

  return (
    <Modal title={sent ? 'Report sent' : 'Send a diagnostic report'} onClose={onClose} width={560}>
      {sent ? (
        <div className="settings-section">
          <div className="settings-info" role="status">
            <div>
              <div className="settings-info-title">Report ID: <span className="mono">{sent.id}</span></div>
              Tell this ID to whoever looks after Presentia so they can find your report.
              It is kept at {reportsHost(getMode())} for 30 days.
            </div>
          </div>
          <div className="diag-actions">
            <button className="btn-ghost" onClick={() => copyText(sent.id).then((ok) => ok && setNote('ID copied.'))}>Copy ID</button>
            <button className="btn-primary" onClick={onClose}>Done</button>
          </div>
          {note && <div className="settings-msg" role="status">{note}</div>}
        </div>
      ) : (
        <div className="settings-section">
          {problem && (
            <div className="settings-msg settings-msg-error">
              <strong>{problem.title} failed.</strong> {problem.message}
            </div>
          )}
          <span className="settings-row-sub" style={{ margin: 0 }}>
            This is everything that will be sent. It can include class names, student names and numbers
            that appeared in the log. Your computer user name has been replaced with &lt;you&gt;.
            Reports are sent to the Presentia team at <span className="mono">{reportsHost(getMode())}</span> and
            deleted after 30 days.
          </span>
          {text === null && !error && (
            <div className="progress-wrap"><span className="spinner" /><span className="progress-label">Preparing the report…</span></div>
          )}
          {text !== null && <pre className="report-preview" tabIndex={0}>{text}</pre>}
          {error && <div className="settings-msg settings-msg-error" role="alert">{error}</div>}
          {note && <div className="settings-msg" role="status">{note}</div>}
          <div className="diag-actions">
            <button className="btn-ghost" onClick={copy} disabled={!text}>Copy</button>
            <button className="btn-ghost" onClick={save} disabled={!text}>Save…</button>
            <button className="btn-primary" onClick={send} disabled={!text || busy}>
              {busy ? 'Sending…' : 'Send report'}
            </button>
          </div>
        </div>
      )}
    </Modal>
  )
}
