/** A class as the sidecar returns it (see /api/classes). */
export interface ClassInfo {
  id: number
  name: string
  section: string
  /** Six characters, no look-alikes. Students will enter it on the website. */
  join_code: string
  created_at: string
  last_opened_at: string | null
  student_count: number
  session_count: number
  last_session_at: string | null
  /** 1 when students can register on the website with the join code. */
  online?: number
  /** Website registrations waiting to be accepted. */
  pending_count?: number
}

/** "K7MQ2P" → "K7M-Q2P", easier to read aloud or copy from a screen. */
export function formatJoinCode(code: string): string {
  return code.length === 6 ? `${code.slice(0, 3)}-${code.slice(3)}` : code
}

/** "IT 101 · BSIT 2A", or just the name when there is no section. */
export function classLabel(c: Pick<ClassInfo, 'name' | 'section'>): string {
  return c.section ? `${c.name} · ${c.section}` : c.name
}

/** Friendly relative date for the start screen ("today", "yesterday", "Oct 3"). */
export function shortDate(stamp: string | null): string {
  if (!stamp) return ''
  // The sidecar stores local time as "YYYY-MM-DD HH:MM:SS".
  const d = new Date(stamp.replace(' ', 'T'))
  if (isNaN(d.getTime())) return stamp
  const today = new Date()
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((startOf(today) - startOf(d)) / 86_400_000)
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  const sameYear = d.getFullYear() === today.getFullYear()
  return d.toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }),
  })
}

/** "2026-10-05 15:20:23" → "Oct 5, 2026". */
export function formatDay(stamp: string | null): string {
  if (!stamp) return ''
  const d = new Date(stamp.replace(' ', 'T'))
  if (isNaN(d.getTime())) return stamp
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

/** "2026-10-05 15:20:23" → "Oct 5, 2026, 3:20 PM". */
export function formatDateTime(stamp: string | null): string {
  if (!stamp) return ''
  const d = new Date(stamp.replace(' ', 'T'))
  if (isNaN(d.getTime())) return stamp
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

/** "2026-10-05 15:20:23" → "3:20 PM". */
export function formatTime(stamp: string | null): string {
  if (!stamp) return ''
  const d = new Date(stamp.replace(' ', 'T'))
  if (isNaN(d.getTime())) return stamp
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}
