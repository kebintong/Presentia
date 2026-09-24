import { useCallback, useEffect, useState } from 'react'

const API = 'http://127.0.0.1:7788'

interface GpuDevice {
  id: string        // "gpu:<index>"
  index: number
  name: string
  vram_mb: number
  backend: 'DirectML' | 'CUDA'
  usable: boolean   // this build can drive it
}

interface PerfInfo {
  settings: { device: string; high_performance: boolean }
  devices: GpuDevice[]
  gpu_runtime: string | null
  status: {
    state: 'loading' | 'applying' | 'ready' | 'error'
    backend?: string
    device?: string
    threads?: number
    fell_back?: boolean
    error?: string | null
    message?: string
  }
  cpu_cores: number
  threads: { balanced: number; high: number }
}

function vram(mb: number) {
  if (!mb) return ''
  return mb >= 1024 ? ` · ${Math.round(mb / 1024)} GB` : ` · ${mb} MB`
}

/**
 * Settings → Performance: which device runs face recognition, and whether
 * to favour recognition speed over leaving resources for other programs.
 * The device list is whatever graphics adapters this computer reports.
 */
export default function PerformanceSettings() {
  const [info, setInfo] = useState<PerfInfo | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/perf`)
      if (!res.ok) throw new Error(`status ${res.status}`)
      setInfo(await res.json())
      setError('')
    } catch {
      setError('Performance settings are unavailable until the engine has started.')
    }
  }, [])

  useEffect(() => { load() }, [load])

  // While the models reload on the new device, keep the status line current.
  const busy = info?.status.state === 'applying' || info?.status.state === 'loading'
  useEffect(() => {
    if (!busy) return
    const t = setInterval(load, 1000)
    return () => clearInterval(t)
  }, [busy, load])

  const save = async (patch: { device?: string; high_performance?: boolean }) => {
    setSaving(true)
    try {
      const res = await fetch(`${API}/api/perf`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) throw new Error(await res.text())
      setInfo(await res.json())
      setError('')
    } catch {
      setError('Could not save the performance settings.')
    } finally {
      setSaving(false)
    }
  }

  const st = info?.status
  const usable = (info?.devices || []).filter((d) => d.usable)
  const best = usable.length ? usable.reduce((a, b) => (b.vram_mb > a.vram_mb ? b : a)) : null

  let summary = 'Checking your hardware…'
  if (st?.state === 'applying') summary = 'Applying — reloading the face models on the selected device…'
  else if (st?.state === 'loading') summary = 'Loading the face models…'
  else if (st?.state === 'error') summary = `Could not switch devices: ${st.message || 'unknown error'}`
  else if (st?.backend && st.backend !== 'CPU') summary = `Face recognition is running on your ${st.device} through ${st.backend}.`
  else if (st?.backend === 'CPU') summary = `Face recognition is running on the CPU (${st.threads} of ${info?.cpu_cores} cores).`

  let note = ''
  if (st?.fell_back && st.backend === 'CPU' && info?.settings.device !== 'cpu') {
    note = 'The selected graphics card could not run the models, so the CPU is being used instead.'
  } else if (info && info.devices.length > 0 && !info.gpu_runtime) {
    note = 'This build cannot use the graphics card yet (it needs the DirectML runtime), so the CPU is used.'
  }

  return (
    <div className="settings-section">
      <span className="field-label">Performance</span>

      <div className="settings-info" role="status">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}>
          <circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" />
        </svg>
        <div>
          <div className="settings-info-title">Processing device info</div>
          <div>{error || summary}</div>
          {note && <div className="settings-info-note">{note}</div>}
        </div>
      </div>

      <div className="settings-row">
        <div>
          <div className="settings-row-title">Processing device</div>
          <div className="settings-row-sub">
            Runs face detection and recognition. Auto picks the most capable graphics card on this computer.
          </div>
        </div>
        <select
          className="input settings-select"
          value={info?.settings.device ?? 'auto'}
          disabled={!info || saving || busy}
          onChange={(e) => save({ device: e.target.value })}
          aria-label="Processing device"
        >
          <option value="auto">Auto{best ? ` (${best.name})` : ' (CPU)'}</option>
          {(info?.devices || []).map((d) => (
            <option key={d.id} value={d.id} disabled={!d.usable}>
              {d.name}{vram(d.vram_mb)}{d.usable ? '' : ' — not supported by this build'}
            </option>
          ))}
          <option value="cpu">CPU only</option>
        </select>
      </div>

      <div className="settings-row">
        <div>
          <div className="settings-row-title">High performance mode</div>
          <div className="settings-row-sub">
            Recognition runs as often as it can
            {info ? `, on up to ${info.threads.high} CPU threads instead of ${info.threads.balanced},` : ''}
            {' '}at higher priority. Turn on for large classes; it may slow down other programs, including the meeting app.
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={!!info?.settings.high_performance}
          aria-label="High performance mode"
          className={`toggle-switch ${info?.settings.high_performance ? 'on' : ''}`}
          disabled={!info || saving || busy}
          onClick={() => save({ high_performance: !info?.settings.high_performance })}
        >
          <span className="toggle-knob" />
        </button>
      </div>
    </div>
  )
}
