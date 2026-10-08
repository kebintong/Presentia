import React from 'react'
import { VerifyStudent as RosterStudent } from './VerifyDialog'

interface RosterListProps {
  students: RosterStudent[]
  onStudentClick?: (student: RosterStudent) => void
  verifyingId?: number | null
}

// Label, badge style and explanation for each state (app/core/roster_monitor.py).
const STATE: Record<RosterStudent['state'], { label: string; badge: string; title: string }> = {
  present: { label: 'PRESENT', badge: 'present', title: 'Recognised on camera' },
  unclear: { label: 'ON CAMERA', badge: 'present', title: 'Their camera is on, but the face is not clear (turned or partly out of view). Counted as present.' },
  unseen:  { label: 'NOT SEEN', badge: 'warn', title: 'Their face has not been seen for a while. Their camera may be off, or they are out of view.' },
  cam_off: { label: 'CAMERA OFF', badge: 'missing', title: 'Their tile shows the meeting\'s camera-off picture.' },
  missing: { label: 'MISSING', badge: 'missing', title: 'Not seen on screen' },
  waiting: { label: 'waiting', badge: 'waiting', title: 'Not seen yet in this meeting' },
}

export default function RosterList({ students, onStudentClick, verifyingId }: RosterListProps) {
  if (students.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '20px 0', fontSize: 13, color: 'var(--muted)' }}>
        No students registered yet
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5, overflowY: 'auto', maxHeight: 220 }}>
      {students.map((s) => (
        <button
          key={s.id}
          onClick={() => onStudentClick?.(s)}
          title={`Verify ${s.name}`}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '9px 12px', borderRadius: 'var(--radius-sm)', width: '100%',
            background: verifyingId === s.id ? 'var(--warn-dim)' : 'var(--card-row-bg)',
            border: `1px solid ${verifyingId === s.id ? 'var(--warn)' : 'var(--glass-border)'}`,
            color: 'var(--ink)', cursor: 'pointer', fontFamily: 'var(--body)',
            transition: 'all 0.15s ease',
          }}
        >
          <span style={{ fontSize: 13.5, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {s.name}
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
            {s.checking ? (
              <span className="badge badge-emerald" title="Liveness check running">CHECKING</span>
            ) : s.suspect && (
              <span className="badge badge-warn" title="Their video has barely changed — it may be a photo or a frozen feed. Click to check.">
                CHECK?
              </span>
            )}
            {s.verified && (
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--present)" strokeWidth="3">
                <path d="M20 6L9 17l-5-5" />
              </svg>
            )}
            <span className={`badge badge-${(STATE[s.state] ?? STATE.waiting).badge}`}
                  title={(STATE[s.state] ?? STATE.waiting).title}>
              {(STATE[s.state] ?? STATE.waiting).label}
            </span>
          </div>
        </button>
      ))}
    </div>
  )
}
