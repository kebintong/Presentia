import React, { useEffect, useRef } from 'react'
import { decodeFrame, type Frame, type FrameFeed } from './frameFeed'

interface Annotation {
  bbox: [number, number, number, number]
  label: string
  color: string
}

interface VideoCanvasProps {
  /** Live frames, drawn as they arrive without re-rendering the page. */
  feed?: FrameFeed
  /** A single base64 JPEG (legacy / still images). */
  jpegBase64?: string | null
  annotations?: Annotation[]
  idle?: boolean
  idleText?: string
  className?: string
}

/**
 * Renders JPEG frames onto a <canvas>, with optional bounding-box overlays,
 * or an idle placeholder.
 *
 * With `feed`, each frame is decoded off the UI thread (createImageBitmap)
 * and drawn directly. If frames arrive faster than they can be decoded, the
 * in-between ones are skipped, so the picture never falls behind.
 */
export default function VideoCanvas({
  feed,
  jpegBase64,
  annotations = [],
  idle = false,
  idleText = 'Camera feed will appear here',
  className = '',
}: VideoCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const annotationsRef = useRef(annotations)
  annotationsRef.current = annotations

  const showIdle = idle || (!feed && !jpegBase64)

  useEffect(() => {
    if (showIdle) return
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return

    let busy = false
    let pending: Frame | undefined
    let alive = true

    const draw = (img: ImageBitmap | HTMLImageElement) => {
      const w = img.width
      const h = img.height
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w
        canvas.height = h
      }
      ctx.drawImage(img, 0, 0)
      for (const ann of annotationsRef.current) {
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

    const show = async (frame: Frame) => {
      if (frame === null) return
      if (busy) {
        pending = frame // keep only the newest
        return
      }
      busy = true
      try {
        const img = await decodeFrame(frame)
        if (alive) draw(img)
        if ('close' in img) img.close()
      } catch {
        /* a corrupt frame — skip it */
      } finally {
        busy = false
      }
      if (alive && pending !== undefined) {
        const next = pending
        pending = undefined
        show(next)
      }
    }

    if (feed) {
      show(feed.latest)
      const off = feed.subscribe(show)
      return () => { alive = false; off() }
    }
    show(jpegBase64 ?? null)
    return () => { alive = false }
  }, [feed, jpegBase64, showIdle])

  if (showIdle) {
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
        background: 'var(--video-idle-bg)',
        display: 'block',
      }}
    />
  )
}
