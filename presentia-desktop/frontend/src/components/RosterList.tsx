import React from 'react'

interface RosterStudent {
  id: number
  name: string
  state: 'present' | 'missing' | 'waiting'
  verified?: boolean
}

interface RosterListProps {
  students: RosterStudent[]
  onStudentClick?: (student: RosterStudent) => void
  verifyingId?: number | null
}

const STATE_LABEL: Record<RosterStudent['state'], string> = {
  present: 'PRESENT',
  missing: 'MISSING',
  waiting: 'waiting',
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
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '9px 12px', borderRadius: 'var(--radius-sm)', width: '100%',
            background: verifyingId === s.id ? 'var(--warn-dim)' : 'var(--glass-lo)',
            border: `1px solid ${verifyingId === s.id ? 'rgba(251,191,36,0.3)' : 'var(--glass-border)'}`,
            color: 'var(--ink)', cursor: 'pointer', fontFamily: 'var(--body)',
            transition: 'all 0.15s ease',
          }}
        >
          <span style={{ fontSize: 13.5, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {s.name}
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
            {s.verified && (
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--present)" strokeWidth="3">
                <path d="M20 6L9 17l-5-5" />
              </svg>
            )}
            <span className={`badge badge-${s.state}`}>{STATE_LABEL[s.state]}</span>
          </div>
        </button>
      ))}
    </div>
  )
}
