import { useCallback, useEffect, useState } from 'react'
import { ClipboardSetText } from '../../wailsjs/runtime/runtime'

const API = 'http://127.0.0.1:7788'

type Status = 'ok' | 'warn' | 'fail' | 'skip'

interface Check {
  id: string
  title: string
  status: Status
  detail: string
  ms: number
}

interface Report {
  generated_at: string
  system: { label: string; value: string }[]
  network: { url: string; checks: Check[]; hints: string[]; trust_in_use: string | null }
  recent: { time: string; source: string; level: string; message: string }[]
  log: string
}

const STATUS_LABEL: Record<Status, string> = { ok: 'OK', warn: 'Note', fail: 'Failed', skip: 'Skipped' }

async function copyText(text: string): Promise<boolean> {
  try {
    if ((window as any)['runtime']) return await ClipboardSetText(text)
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** Plain-text report to paste into a message or an issue. */
function reportText(r: Report, version: string): string {
  const out: string[] = []
  out.push(`Presentia diagnostics — ${r.generated_at}`)
  out.push(`App version: ${version || 'development'}`)
  out.push(`Webview: ${navigator.userAgent}`)
  for (const s of r.system) out.push(`${s.label}: ${s.value}`)
  out.push('', `Registration website: ${r.network.url || '(not set)'}`)
  for (const c of r.network.checks) {
    out.push(`[${STATUS_LABEL[c.status]}] ${c.title}${c.ms ? ` (${c.ms} ms)` : ''}: ${c.detail}`)
  }
  if (r.network.trust_in_use) out.push(`Certificate check in use: ${r.network.trust_in_use}`)
  if (r.network.hints.length) {
    out.push('', 'What to do:')
    for (const h of r.network.hints) out.push(`- ${h}`)
  }
  out.push('', 'Recent problems:')
  if (r.recent.length === 0) out.push('(none since Presentia started)')
  for (const e of r.recent) out.push(`${e.time} [${e.level}] ${e.source}: ${e.message}`)
  if (r.log.trim()) out.push('', 'Sidecar log (last lines):', r.log.trimEnd())
  return out.join('\n')
}

/**
 * Settings → Diagnostics: checks each step of reaching the registration
 * website, shows recent problems, and copies everything as one report.
 * Nothing is sent anywhere.
 */
export default function DiagnosticsSettings({ version }: { version: string }) {
  const [report, setReport] = useState<Report | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  const run = useCallback(async () => {
    setRunning(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/diagnostics`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setReport(await res.json())
    } catch (err: any) {
      setError(
        'The Presentia engine did not answer, so nothing could be checked. ' +
        'Restart Presentia; if this keeps happening, the engine is not starting.'
      )
    } finally {
      setRunning(false)
    }
  }, [])

  useEffect(() => { run() }, [run])

  const copy = async () => {
    if (!report) return
    setCopied(await copyText(reportText(report, version)))
    setTimeout(() => setCopied(false), 2000)
  }

  const failed = report?.network.checks.some((c) => c.status === 'fail')

  return (
    <div className="settings-section diag">
      <div className="diag-head">
        <span className="settings-row-sub" style={{ margin: 0 }}>
          Checks how this computer reaches the registration website and lists recent problems.
          Copy the report and send it to whoever looks after Presentia.
        </span>
        <div className="diag-actions">
          <button className="btn-ghost" onClick={run} disabled={running}>
            {running ? 'Checking…' : 'Run again'}
          </button>
          <button className="btn-primary" onClick={copy} disabled={!report || running}>
            {copied ? 'Copied' : 'Copy report'}
          </button>
        </div>
      </div>

      {error && <div className="settings-msg settings-msg-error">{error}</div>}
      {running && !report && (
        <div className="progress-wrap"><span className="spinner" /><span className="progress-label">Running checks…</span></div>
      )}

      {report && (
        <>
          <div className={`settings-info diag-summary ${failed ? 'bad' : ''}`} role="status">
            <div>
              {report.network.hints.map((h, i) => <div key={i}>{h}</div>)}
            </div>
          </div>

          <span className="field-label">Registration website</span>
          <ul className="diag-list">
            {report.network.checks.map((c) => (
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

          <span className="field-label">This computer</span>
          <dl className="diag-kv">
            <dt>App version</dt><dd>{version || 'development'}</dd>
            {report.system.map((s) => (
              <div key={s.label} style={{ display: 'contents' }}>
                <dt>{s.label}</dt><dd>{s.value}</dd>
              </div>
            ))}
          </dl>

          <span className="field-label">Recent problems</span>
          {report.recent.length === 0 ? (
            <div className="settings-msg">None since Presentia started.</div>
          ) : (
            <ul className="diag-list">
              {report.recent.slice(0, 12).map((e, i) => (
                <li key={i} className="diag-row">
                  <span className={`diag-dot ${e.level === 'error' ? 'fail' : 'warn'}`} />
                  <div className="diag-text">
                    <div className="diag-title">{e.source}<span className="diag-ms">{e.time}</span></div>
                    <div className="diag-detail">{e.message}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {report.log.trim() && (
            <details className="diag-log">
              <summary>Engine log (last lines)</summary>
              <pre>{report.log}</pre>
            </details>
          )}
        </>
      )}
    </div>
  )
}
