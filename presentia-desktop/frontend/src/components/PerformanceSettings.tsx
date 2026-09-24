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

interface BenchResult { id: string; device: string; backend: string; ms?: number; error?: string }

interface PerfInfo {
  settings: { device: string; profile: string }
  effective_profile: 'low' | 'balanced' | 'high'
  profiles: Record<string, { label: string; summary: string }>
  benchmark: {
    results?: BenchResult[]
    best?: string
    best_ms?: number
    recommended?: string
    measured_at?: string
  }
  devices: GpuDevice[]
  gpu_runtime: string | null
  status: {
    state: 'loading' | 'applying' | 'ready' | 'error'
    backend?: string
    device?: string
    threads?: number
    fell_back?: boolean
    profile?: string
    detectors?: { camera: string; meeting: string }
    message?: string
  }
  cpu_cores: number
  ram_gb: number
}

const DETECTOR_NAMES: Record<string, string> = {
  yunet: 'YuNet (tiny)',
  'scrfd_2.5g': 'SCRFD 2.5G (light)',
  scrfd_10g: 'SCRFD 10G (full)',
}

function vram(mb: number) {
  if (!mb) return ''
  return mb >= 1024 ? ` · ${Math.round(mb / 1024)} GB` : ` · ${mb} MB`
}

/**
 * Settings → Performance: which device runs face recognition and which
 * profile (Low / Balanced / High) the app uses. "Auto" follows a short
 * hardware check that times detection on the CPU and every graphics card.
 */
export default function PerformanceSettings() {
  const [info, setInfo] = useState<PerfInfo | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [measuring, setMeasuring] = useState(false)

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

  // While the models reload, keep the status line current.
  const busy = measuring || info?.status.state === 'applying' || info?.status.state === 'loading'
  useEffect(() => {
    if (!busy) return
    const t = setInterval(load, 1000)
    return () => clearInterval(t)
  }, [busy, load])

  const save = async (patch: { device?: string; profile?: string }) => {
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

  const measure = async () => {
    setMeasuring(true)
    try {
      const res = await fetch(`${API}/api/perf/benchmark`, { method: 'POST' })
      if (res.ok) setInfo(await res.json())
    } catch {
      setError('The hardware check could not run.')
    } finally {
      setMeasuring(false)
    }
  }

  const st = info?.status
  const bench = info?.benchmark
  const rec = bench?.recommended
  const profileLabel = (p?: string) => (p && info?.profiles[p]?.label) || p || ''

  let summary = 'Checking your hardware…'
  if (measuring) summary = 'Measuring this computer…'
  else if (st?.state === 'applying') summary = 'Applying — reloading the face models…'
  else if (st?.state === 'loading') summary = st.message || 'Loading the face models…'
  else if (st?.state === 'error') summary = `Could not apply the settings: ${st.message || 'unknown error'}`
  else if (st?.backend && st.backend !== 'CPU') summary = `Face recognition is running on your ${st.device} through ${st.backend}.`
  else if (st?.backend === 'CPU') summary = `Face recognition is running on the CPU (${st.threads} of ${info?.cpu_cores} cores).`

  let note = ''
  if (st?.fell_back && st.backend === 'CPU' && info?.settings.device !== 'cpu') {
    note = 'The selected graphics card could not run the models, so the CPU is being used instead.'
  } else if (info && info.devices.length > 0 && !info.gpu_runtime) {
    note = 'This build cannot use the graphics card (it needs the DirectML runtime), so the CPU is used.'
  }

  const measured = (bench?.results || []).filter((r) => r.ms !== undefined)

  return (
    <div className="settings-section">
      <div className="settings-info" role="status">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}>
          <circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" />
        </svg>
        <div>
          <div className="settings-info-title">Processing device info</div>
          <div>{error || summary}</div>
          {st?.state === 'ready' && info && (
            <div className="settings-info-note">
              Profile: {profileLabel(info.effective_profile)}
              {st.detectors && ` · webcam: ${DETECTOR_NAMES[st.detectors.camera] || st.detectors.camera}`}
              {st.detectors && ` · meetings: ${DETECTOR_NAMES[st.detectors.meeting] || st.detectors.meeting}`}
            </div>
          )}
          {note && <div className="settings-info-note">{note}</div>}
        </div>
      </div>

      <div className="settings-row">
        <div>
          <div className="settings-row-title">Performance profile</div>
          <div className="settings-row-sub">
            {info
              ? info.profiles[info.effective_profile]?.summary
              : 'How much work face recognition does. Auto picks one for this computer.'}
          </div>
        </div>
        <select
          className="input settings-select"
          value={info?.settings.profile ?? 'auto'}
          disabled={!info || saving || busy}
          onChange={(e) => save({ profile: e.target.value })}
          aria-label="Performance profile"
        >
          <option value="auto">Auto{rec ? ` (${profileLabel(rec)})` : ''}</option>
          {info && Object.entries(info.profiles).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>
      </div>

      <div className="settings-row">
        <div>
          <div className="settings-row-title">Processing device</div>
          <div className="settings-row-sub">
            Runs face detection and recognition. Auto uses whichever device measured fastest.
          </div>
        </div>
        <select
          className="input settings-select"
          value={info?.settings.device ?? 'auto'}
          disabled={!info || saving || busy}
          onChange={(e) => save({ device: e.target.value })}
          aria-label="Processing device"
        >
          <option value="auto">Auto</option>
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
          <div className="settings-row-title">Hardware check</div>
          <div className="settings-row-sub">
            {measured.length > 0
              ? measured.map((r) => `${r.id === 'cpu' ? 'CPU' : r.device}: ${r.ms} ms`).join(' · ')
                + (bench?.measured_at ? ` — measured ${bench.measured_at}` : '')
              : 'Not measured yet.'}
            {info ? ` · ${info.cpu_cores} cores, ${info.ram_gb} GB RAM` : ''}
          </div>
        </div>
        <button className="btn-ghost" onClick={measure} disabled={!info || busy}>
          {measuring ? 'Measuring…' : 'Check again'}
        </button>
      </div>
    </div>
  )
}
