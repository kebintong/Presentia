import React from 'react'

interface Alert {
  id: number
  ts: string
  message: string
  level: 'ok' | 'warn' | 'error' | 'info'
}

interface AlertListProps {
  alerts: Alert[]
  maxHeight?: number
}

export default function AlertList({ alerts, maxHeight = 200 }: AlertListProps) {
  if (alerts.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '20px 0', fontSize: 13, color: 'var(--muted)' }}>
        No alerts yet
      </div>
    )
  }

  return (
    <div style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4, maxHeight }}>
      {alerts.map((a) => (
        <div
          key={a.id}
          className={`alert-${a.level}`}
          style={{
            display: 'flex', alignItems: 'flex-start', gap: 8,
            padding: '7px 10px', borderRadius: 8,
            background: 'var(--glass-lo)',
            border: '1px solid var(--glass-border)',
            fontSize: 12, fontWeight: 500,
            animation: 'rise 0.3s ease',
          }}
        >
          <span style={{ opacity: 0.5, fontFamily: 'monospace', flexShrink: 0 }}>[{a.ts}]</span>
          <span style={{ lineHeight: 1.4 }}>{a.message}</span>
        </div>
      ))}
    </div>
  )
}

let _nextId = 1
export function makeAlert(message: string, level: Alert['level'] = 'info'): Alert {
  const now = new Date()
  const ts = `${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}:${now.getSeconds().toString().padStart(2,'0')}`
  return { id: _nextId++, ts, message, level }
}
