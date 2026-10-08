import { useEffect, useMemo, useRef, useState } from 'react'
import Modal from './Modal'

const API = 'http://127.0.0.1:7788'
const goApp = () => (window as any)['go']?.['main']?.['App']

/** A browser window found on screen (from GetOpenWindows). */
export interface BrowserWindow {
  title: string
  exe?: string
  browser?: string
}

interface Status {
  connected: boolean
  sharing: boolean
  ended: boolean
  label: string
  surface: string
  width: number
  height: number
}

interface Props {
  /** Browser windows open now; the meeting's is listed first. */
  windows: BrowserWindow[]
  onUse: (label: string) => void
  onClose: () => void
}

const MEETING = /meet|zoom|teams/i

/**
 * Share the meeting's browser tab with Presentia, the way OBS or Meet's
 * "Present → A tab" does. Presentia opens a page in the meeting's browser;
 * the instructor picks the meeting tab there. A shared tab keeps updating
 * while it is covered, minimised or in the background, so monitoring goes on
 * while the instructor uses other apps. (app/core/tab_feed.py)
 */
export default function TabShareDialog({ windows, onUse, onClose }: Props) {
  const [url, setUrl] = useState('')
  const [status, setStatus] = useState<Status | null>(null)
  const [opened, setOpened] = useState('')
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const used = useRef(false)

  // One button per browser, the one with a meeting open first.
  const browsers = useMemo(() => {
    const seen = new Map<string, BrowserWindow & { meeting: boolean }>()
    for (const w of windows) {
      if (!w.browser || !w.exe) continue
      const key = w.exe.toLowerCase()
      const meeting = MEETING.test(w.title)
      const had = seen.get(key)
      if (!had || (meeting && !had.meeting)) seen.set(key, { ...w, meeting })
    }
    return [...seen.values()].sort((a, b) => Number(b.meeting) - Number(a.meeting))
  }, [windows])

  useEffect(() => {
    let stop = false
    fetch(`${API}/api/tabshare/start`, { method: 'POST' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => { if (!stop) { setUrl(d.url); setStatus(d) } })
      .catch(() => setError('Could not reach the Presentia engine. Is it running?'))
    const timer = setInterval(() => {
      fetch(`${API}/api/tabshare/status`).then((r) => r.json()).then((d) => { if (!stop) setStatus(d) }).catch(() => {})
    }, 800)
    return () => { stop = true; clearInterval(timer) }
  }, [])

  const open = async (b?: BrowserWindow) => {
    if (!url) return
    setError('')
    try {
      const app = goApp()
      if (app?.['OpenInBrowser']) {
        await app['OpenInBrowser'](url, b?.exe ?? '')
      } else {
        window.open(url, '_blank')
      }
      setOpened(b?.browser || 'your browser')
    } catch (e: any) {
      setError(`Could not open the browser: ${e?.message ?? e}. Copy the link instead.`)
    }
  }

  const copy = async () => {
    try {
      const app = (window as any)['runtime']
      if (app?.ClipboardSetText) await app.ClipboardSetText(url)
      else await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setError('Could not copy the link.')
    }
  }

  const sharing = !!status?.sharing
  // Browsers don't say which tab is shared; the meeting's window title is
  // the best name (e.g. "Meet - aic-ijui-fyo - Brave" → "Meet - aic-ijui-fyo").
  const meetingTitle = useMemo(() => {
    const w = windows.find((x) => x.browser && MEETING.test(x.title))
    return w ? w.title.replace(/\s+-\s+[^-]+$/, '') : ''
  }, [windows])
  const label = status?.label || meetingTitle || 'The meeting tab'
  const notTab = sharing && status?.surface && status.surface !== 'browser'

  const use = () => {
    if (used.current) return
    used.current = true
    onUse(label)
  }

  return (
    <Modal title="Share the meeting tab" onClose={onClose} width={500}
      icon={<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>}>
      <div className="check-body">
        <p>
          Presentia watches the <strong>tab itself</strong>, like sharing a tab in Google Meet, so monitoring
          keeps going while you switch to other apps or tabs. It works in Chrome, Edge and Brave.
        </p>

        <div>
          <span className="field-label">1 · Open the share page in your meeting's browser</span>
          <div className="tabshare-buttons">
            {browsers.map((b) => (
              <button key={b.exe} className={b.meeting ? 'btn-primary' : 'btn-ghost'} onClick={() => open(b)}
                      disabled={!url} title={b.title}>
                Open in {b.browser}
              </button>
            ))}
            <button className={browsers.length ? 'btn-ghost' : 'btn-primary'} onClick={() => open()} disabled={!url}>
              {browsers.length ? 'Default browser' : 'Open the share page'}
            </button>
            <button className="btn-ghost" onClick={copy} disabled={!url}>{copied ? 'Copied ✓' : 'Copy link'}</button>
          </div>
          {opened && !sharing && (
            <div className="check-muted" style={{ marginTop: 6 }}>
              Opened in {opened}. If it's not the browser your meeting is in, use Copy link and paste it there.
            </div>
          )}
        </div>

        <div>
          <span className="field-label">2 · On that page, choose the meeting tab and press Share</span>
          <div className={`tabshare-status ${sharing ? 'ok' : ''}`}>
            <span className="tabshare-dot" />
            <span>
              {sharing
                ? <><strong>Sharing:</strong> {label}{status?.width ? ` · ${status.width}×${status.height}` : ''}</>
                : status?.connected
                  ? 'The share page is open — choose the meeting tab there.'
                  : status?.ended
                    ? 'Sharing stopped. Choose the tab again on the share page.'
                    : 'Waiting for the share page…'}
            </span>
          </div>
          {notTab && (
            <div className="check-note warn" style={{ marginTop: 8 }}>
              A {status?.surface === 'monitor' ? 'whole screen' : 'window'} is being shared, not a tab. A covered
              window stops updating; choose the meeting <strong>tab</strong> for monitoring in the background.
            </div>
          )}
        </div>

        {error && <div className="check-note warn">{error}</div>}

        <div className="check-actions">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={use} disabled={!sharing}>Use this tab</button>
        </div>
      </div>
    </Modal>
  )
}
