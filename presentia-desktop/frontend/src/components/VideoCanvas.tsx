import React, { useEffect, useRef } from 'react'

interface Annotation {
  bbox: [number, number, number, number]
  label: string
  color: string
}

interface VideoCanvasProps {
  jpegBase64?: string | null
  annotations?: Annotation[]
  idle?: boolean
  idleText?: string
  className?: string
}

/**
 * Renders JPEG frames (base64) onto a <canvas> with optional bounding-box
 * annotation overlays. Falls back to a glass idle placeholder.
 */
export default function VideoCanvas({
  jpegBase64,
  annotations = [],
  idle = false,
  idleText = 'Camera feed will appear here',
  className = '',
}: VideoCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement>(new Image())

  useEffect(() => {
    if (!jpegBase64 || idle) return
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const img = imgRef.current
    img.onload = () => {
      canvas.width = img.width
      canvas.height = img.height
      ctx.drawImage(img, 0, 0)

      for (const ann of annotations) {
        const [x1, y1, x2, y2] = ann.bbox
        ctx.strokeStyle = ann.color
        ctx.lineWidth = 2
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)

        ctx.font = '600 12px Manrope, sans-serif'
        const tw = ctx.measureText(ann.label).width
        ctx.fillStyle = ann.color + 'cc'
        ctx.fillRect(x1, Math.max(0, y1 - 20), tw + 10, 20)
        ctx.fillStyle = '#fff'
        ctx.fillText(ann.label, x1 + 5, Math.max(13, y1 - 4))
      }
    }
    img.src = 'data:image/jpeg;base64,' + jpegBase64
  }, [jpegBase64, annotations, idle])

  if (idle || !jpegBase64) {
    return (
      <div
        className={`video-idle glass-panel ${className}`}
        style={{ minHeight: 220 }}
      >
        <svg
          width="44" height="44" viewBox="0 0 24 24" fill="none"
          stroke="var(--muted)" strokeWidth="1.4"
          style={{ opacity: 0.5 }}
        >
          <path d="M15 10l4.553-2.069A1 1 0 0121 8.867V15.133a1 1 0 01-1.447.936L15 14M3 8a2 2 0 012-2h10a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z" />
        </svg>
        <p style={{ fontSize: 13, color: 'var(--muted)', textAlign: 'center', padding: '0 20px', maxWidth: 240 }}>
          {idleText}
        </p>
      </div>
    )
  }

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{
        width: '100%',
        height: 'auto',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--glass-border)',
        background: '#080b12',
        display: 'block',
      }}
    />
  )
}
