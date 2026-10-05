import { useCallback, useEffect, useState } from 'react'
import Modal from './Modal'
import ReportDialog from './ReportDialog'
import {
  DiagMode, DiagnosticsData, STATUS_LABEL, buildReport, copyText, fetchActivityLog, fetchDiagnostics,
  getMode, onMode, redact, reportFileName, reportsHost, saveText, setDiagnosticMode,
} from '../diagnostics'

const fmt = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''

const kb = (bytes: number) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`)

/**
 * Settings → Diagnostics: diagnostic mode (with what it does and its
 * downsides), the activity log, reporting a problem, and a connection check.
 */
export default function DiagnosticsSettings({ version }: { version: string }) {
  const [mode, setModeState] = useState<DiagMode>(getMode())
  const [confirming, setConfirming] = useState(false)
  const [modeBusy, setModeBusy] = useState(false)
  const [log, setLog] = useState<{ text: string; path: string; size: number } | null>(null)
  const [data, setData] = useState<DiagnosticsData | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [reporting, setReporting] = useState(false)

  useEffect(() => onMode(setModeState), [])

  const loadLog = useCallback(async () => {
    try { setLog(await fetchActivityLog(200)) } catch { setLog(null) }
  }, [])

  const runChecks = useCallback(async () => {
    setRunning(true)
    setError('')
    try {
      const d = await fetchDiagnostics(true)
      setData(d)
      setModeState(d.mode)
    } catch {
      setError('The Presentia engine did not answer, so nothing could be checked. Restart Presentia; if this keeps happening, the engine is not starting.')
    } finally {
      setRunning(false)
    }
  }, [])

  useEffect(() => { loadLog() }, [loadLog, mode.enabled])

  const turn = async (on: boolean) => {
    setModeBusy(true)
    setNote('')
    try {
      await setDiagnosticMode(on)
      setConfirming(false)
      setNote(on ? 'Diagnostic mode is on.' : 'Diagnostic mode is off. The activity log is kept until you delete it.')
      loadLog()
    } catch {
      setNote('Could not change diagnostic mode: the engine is not answering.')
    } finally {
      setModeBusy(false)
    }
  }

  const act = async (what: 'copy-log' | 'save-log' | 'delete-log' | 'copy-report' | 'save-report') => {
    setNote('')
    try {
      if (what === 'copy-log' || what === 'save-log') {
        const full = redact((await fetchActivityLog(5000)).text)
        if (what === 'copy-log') setNote((await copyText(full)) ? 'Activity log copied.' : 'Could not copy.')
        else {
          const where = await saveText(reportFileName('presentia-activity-log'), full)
          if (where) setNote(`Saved to ${where}.`)
        }
      } else if (what === 'delete-log') {
        await fetch('http://127.0.0.1:7788/api/diagnostics/log', { method: 'DELETE' })
        setNote('Activity log deleted.')
        loadLog()
      } else {
        const text = await buildReport(version, null)
        if (what === 'copy-report') setNote((await copyText(text)) ? 'Report copied.' : 'Could not copy.')
        else {
          const where = await saveText(reportFileName(), text)
          if (where) setNote(`Saved to ${where}.`)
        }
      }
    } catch {
      setNote('That did not work: the Presentia engine is not answering.')
    }
  }

  const failed = data?.network?.checks.some((c) => c.status === 'fail')

  return (
    <div className="settings-section diag">
      {/* ── Diagnostic mode ─────────────────────────────────────────── */}
      <span className="field-label">Diagnostic mode</span>
      <div className="settings-row">
        <div>
          <div className="settings-row-title">Diagnostic mode</div>
          <div className="settings-row-sub">
            {mode.enabled
              ? `On since ${fmt(mode.since)}. Turns itself off ${fmt(mode.until)}.`
              : mode.expired
                ? 'Turned itself off after 7 days.'
                : 'Keeps a detailed log and offers a report when something fails.'}
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={mode.enabled}
          aria-label="Diagnostic mode"
          className={`toggle-switch ${mode.enabled ? 'on' : ''}`}
          disabled={modeBusy}
          onClick={() => (mode.enabled ? turn(false) : setConfirming(true))}
        >
          <span className="toggle-knob" />
        </button>
      </div>
      {note && <div className="settings-msg" role="status">{note}</div>}

      {/* ── Activity log ────────────────────────────────────────────── */}
      {(mode.enabled || (log && log.size > 0)) && (
        <>
          <span className="field-label">Activity log</span>
          <div className="diag-logbox">
            <pre className="diag-logtext" tabIndex={0}>
              {log?.text.trim() ? redact(log.text) : 'Nothing logged yet. Use Presentia as usual; activity appears here.'}
            </pre>
            <div className="diag-logbar">
              <span className="diag-ms">{log ? `${kb(log.size)} · ${redact(log.path)}` : ''}</span>
              <div className="diag-actions">
                <button className="btn-ghost" onClick={loadLog}>Refresh</button>
                <button className="btn-ghost" onClick={() => act('copy-log')} disabled={!log?.size}>Copy</button>
                <button className="btn-ghost" onClick={() => act('save-log')} disabled={!log?.size}>Save…</button>
                <button className="btn-ghost danger-text" onClick={() => act('delete-log')} disabled={!log?.size}>Delete</button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* ── Report a problem ────────────────────────────────────────── */}
      <span className="field-label">Report a problem</span>
      <div className="settings-row">
        <div>
          <div className="settings-row-title">Diagnostic report</div>
          <div className="settings-row-sub">
            Details about this computer, the connection to the registration website, recent problems
            and the activity log, in one text. You see it before anything is sent. Reports go to the
            Presentia team at <span className="mono">{reportsHost(mode)}</span>, whatever registration website is set.
          </div>
        </div>
      </div>
      <div className="diag-actions" style={{ justifyContent: 'flex-start' }}>
        <button className="btn-ghost" onClick={() => act('copy-report')}>Copy report</button>
        <button className="btn-ghost" onClick={() => act('save-report')}>Save report…</button>
        <button className="btn-primary" onClick={() => setReporting(true)}>Send report…</button>
      </div>

      {/* ── Connection check ────────────────────────────────────────── */}
      <span className="field-label">Registration website connection</span>
      {!data && !running && (
        <div className="diag-actions" style={{ justifyContent: 'flex-start' }}>
          <button className="btn-ghost" onClick={runChecks}>Check the connection</button>
        </div>
      )}
      {running && (
        <div className="progress-wrap"><span className="spinner" /><span className="progress-label">Checking…</span></div>
      )}
      {error && <div className="settings-msg settings-msg-error">{error}</div>}
      {data?.network && !running && (
        <>
          <div className={`settings-info diag-summary ${failed ? 'bad' : ''}`} role="status">
            <div>{data.network.hints.map((h, i) => <div key={i}>{h}</div>)}</div>
          </div>
          <ul className="diag-list">
            {data.network.checks.map((c) => (
              <li key={c.id} className="diag-row">
                <span className={`diag-dot ${c.status}`} aria-label={STATUS_LABEL[c.status]} title={STATUS_LABEL[c.status]} />
                <div className="diag-text">
                  <div className="diag-title">
                    {c.title}
                    {c.ms > 0 && <span className="diag-ms">{c.ms} ms</span>}
                  </div>
                  <div className="diag-detail">{c.detail}</div>
                </div>
              </li>
            ))}
          </ul>
          <div className="diag-actions" style={{ justifyContent: 'flex-start' }}>
            <button className="btn-ghost" onClick={runChecks}>Check again</button>
          </div>

          <span className="field-label">This computer</span>
          <dl className="diag-kv">
            <dt>App version</dt><dd>{version || 'development'}</dd>
            {data.system.map((s) => (
              <div key={s.label} style={{ display: 'contents' }}>
                <dt>{s.label}</dt><dd>{redact(s.value)}</dd>
              </div>
            ))}
          </dl>

          <span className="field-label">Recent problems</span>
          {data.recent.length === 0 ? (
            <div className="settings-msg">None since Presentia started.</div>
          ) : (
            <ul className="diag-list">
              {data.recent.slice(0, 12).map((e, i) => (
                <li key={i} className="diag-row">
                  <span className={`diag-dot ${e.level === 'error' ? 'fail' : 'warn'}`} />
                  <div className="diag-text">
                    <div className="diag-title">{e.source}<span className="diag-ms">{e.time}</span></div>
                    <div className="diag-detail">{redact(e.message)}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {confirming && (
        <Modal title="Turn on diagnostic mode?" onClose={() => setConfirming(false)} width={500}>
          <div className="settings-section diag-confirm">
            <p>Diagnostic mode helps find out why something in Presentia is not working.</p>
            <div>
              <div className="settings-row-title">What happens</div>
              <ul>
                <li>Presentia keeps a detailed <strong>activity log</strong> on this computer: what it asks the
                  engine and the registration website, how long that takes, and every error.</li>
                <li>When something fails, a small box in the corner offers to <strong>send a report</strong>,
                  copy it or save it. You always see the report before anything is sent.</li>
                <li>It <strong>turns itself off after 7 days</strong>, or whenever you switch it off here.</li>
              </ul>
            </div>
            <div>
              <div className="settings-row-title">Things to know</div>
              <ul>
                <li>Error boxes can appear <strong>during a class</strong>. Close them with ✕; the same error
                  will not pop up again for 2 minutes.</li>
                <li>The log can contain <strong>class names, student names and student numbers</strong>, the
                  registration website address and folder names. Only send reports to someone you trust with that.</li>
                <li>The log uses <strong>up to about 6 MB</strong> of disk space (older lines are removed), and
                  Presentia does a little extra work writing it.</li>
                <li>Nothing leaves this computer unless you press <strong>Send report</strong>. Sent reports are
                  kept by the Presentia team for 30 days.</li>
              </ul>
            </div>
            <div className="diag-actions">
              <button className="btn-ghost" onClick={() => setConfirming(false)}>Cancel</button>
              <button className="btn-primary" onClick={() => turn(true)} disabled={modeBusy}>
                {modeBusy ? 'Turning on…' : 'Turn on'}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {reporting && <ReportDialog version={version} problem={null} onClose={() => setReporting(false)} />}
    </div>
  )
}
