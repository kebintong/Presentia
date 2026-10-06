import { useState } from 'react'
import ReleaseNotes from './ReleaseNotes'

export interface ReleaseNote {
  version: string
  notes: string
  date: string
}

const OPEN_AT_START = 2  // newest versions shown opened
const LISTED = 5         // versions listed before "and N earlier updates"

function month(date: string): string {
  const d = new Date(date)
  return isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
}

/**
 * Settings → Updates: the notes of every version the computer is missing,
 * newest first. Each version opens and closes; a long gap is shortened to
 * the newest few plus "and N earlier updates · Show all".
 */
export default function UpdateHistory({ releases }: { releases: ReleaseNote[] }) {
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(releases.slice(0, OPEN_AT_START).map((r) => r.version)),
  )
  const [showAll, setShowAll] = useState(false)

  const toggle = (v: string) => setOpen((cur) => {
    const next = new Set(cur)
    if (next.has(v)) next.delete(v)
    else next.add(v)
    return next
  })

  const shown = showAll ? releases : releases.slice(0, LISTED)
  const hidden = releases.slice(shown.length)

  return (
    <div className="update-history">
      {shown.map((r, i) => {
        const isOpen = open.has(r.version)
        const id = `release-${r.version.replace(/\W/g, '-')}`
        return (
          <section key={r.version} className={`update-version ${isOpen ? 'open' : ''}`}>
            <button
              type="button"
              className="update-version-head"
              aria-expanded={isOpen}
              aria-controls={id}
              onClick={() => toggle(r.version)}
            >
              <svg className="update-version-chev" width="12" height="12" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 6l6 6-6 6" />
              </svg>
              <span className="update-version-name">{r.version}</span>
              {i === 0 && <span className="update-pill">Newest</span>}
              <span className="update-version-date">{month(r.date)}</span>
            </button>
            {isOpen && (
              <div className="update-version-body" id={id}>
                <ReleaseNotes body={r.notes} empty="No details were given for this version." />
              </div>
            )}
          </section>
        )
      })}
      {hidden.length > 0 && (
        <div className="update-history-more">
          and {hidden.length} earlier {hidden.length === 1 ? 'update' : 'updates'}
          {hidden.length > 1 && ` (${hidden[hidden.length - 1].version} – ${hidden[0].version})`}
          {' · '}
          <button type="button" className="link-button" onClick={() => setShowAll(true)}>Show all</button>
        </div>
      )}
    </div>
  )
}
