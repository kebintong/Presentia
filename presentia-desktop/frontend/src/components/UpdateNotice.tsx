import { useEffect, useState } from 'react'
import type { UpdateInfo } from './SettingsPanel'
import { teacherNotes } from './ReleaseNotes'

const API = 'http://127.0.0.1:7788'
const LATER_KEY = 'presentia.updateLater'
const LATER_HOURS = 24
const MONITOR_POLL_MS = 60_000

function laterUntil(version: string): number {
  try {
    const v = JSON.parse(localStorage.getItem(LATER_KEY) || 'null')
    return v && v.version === version ? Number(v.until) || 0 : 0
  } catch {
    return 0
  }
}

/** First sentence of the release notes, as plain text, for the notice. */
function summary(notes: string): string {
  const first = teacherNotes(notes).split('\n').map((l) => l.trim()).find(Boolean) || ''
  return first
    .replace(/^#+\s*|^[-*+]\s+/, '')
    .replace(/[*`]/g, '')
    .replace(/(^|\W)_+|_+(\W|$)/g, '$1$2')
}

/**
 * A small notice in the corner when a newer version is published. It never
 * appears while a meeting is being monitored (it waits until monitoring
 * stops), and "Later" hides it for a day for that version. Nothing is
 * downloaded or installed unless the teacher chooses to in Settings.
 */
export default function UpdateNotice({
  update, hidden, onOpen,
}: {
  update: UpdateInfo | null
  hidden: boolean
  onOpen: () => void
}) {
  const version = update?.available ? update.latest : ''
  const [later, setLater] = useState(0)
  const [monitoring, setMonitoring] = useState(true) // assume busy until known
  const [now, setNow] = useState(Date.now())

  useEffect(() => { setLater(version ? laterUntil(version) : 0) }, [version])

  // Is a meeting being monitored? Asked once a minute while there is
  // something to show; an unreachable engine counts as "not monitoring".
  useEffect(() => {
    if (!version) return
    let cancelled = false
    const ask = async () => {
      try {
        const res = await fetch(`${API}/api/monitor/live`)
        const live = res.ok ? await res.json() : null
        if (!cancelled) setMonitoring(!!live?.active)
      } catch {
        if (!cancelled) setMonitoring(false)
      }
      if (!cancelled) setNow(Date.now())
    }
    ask()
    const t = setInterval(ask, MONITOR_POLL_MS)
    return () => { cancelled = true; clearInterval(t) }
  }, [version])

  if (!version || hidden || monitoring || later > now) return null

  const dismiss = () => {
    const until = Date.now() + LATER_HOURS * 3600_000
    try { localStorage.setItem(LATER_KEY, JSON.stringify({ version, until })) } catch { /* ignore */ }
    setLater(until)
  }

  const missed = update?.releases?.length ?? 0
  const gist = summary(update?.notes || '')
  const text = missed > 1
    ? `${missed} updates since your version.${gist ? ` Newest: ${gist}` : ''}`
    : gist

  return (
    <div className="update-notice" role="status" aria-live="polite">
      <div>
        <div className="update-notice-title">Presentia {version} is available</div>
        <div className="update-notice-text">
          {text || `You have ${update?.current || 'an older version'}.`}
        </div>
      </div>
      <div className="update-notice-actions">
        <button className="btn-ghost" onClick={dismiss}>Later</button>
        <button className="btn-primary" onClick={onOpen}>See what's new</button>
      </div>
    </div>
  )
}
