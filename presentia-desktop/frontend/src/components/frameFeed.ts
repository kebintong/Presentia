import { useRef } from 'react'

/**
 * A live video frame: raw JPEG bytes from the sidecar's binary WebSocket
 * messages, or a base64 JPEG string (imported photos). null = no picture.
 */
export type Frame = ArrayBuffer | string | null

/**
 * Carries frames from a WebSocket straight to the canvases that draw them,
 * without going through React state. Putting every frame in state made the
 * whole page re-render 20–30 times a second, which is where most of the
 * preview stutter came from.
 */
export interface FrameFeed {
  push(frame: Frame): void
  subscribe(fn: (frame: Frame) => void): () => void
  readonly latest: Frame
}

export function createFrameFeed(): FrameFeed {
  const listeners = new Set<(frame: Frame) => void>()
  let latest: Frame = null
  return {
    push(frame) {
      latest = frame
      listeners.forEach((fn) => fn(frame))
    },
    subscribe(fn) {
      listeners.add(fn)
      return () => { listeners.delete(fn) }
    },
    get latest() {
      return latest
    },
  }
}

/** One feed for the lifetime of a component. */
export function useFrameFeed(): FrameFeed {
  const ref = useRef<FrameFeed | null>(null)
  if (ref.current === null) ref.current = createFrameFeed()
  return ref.current
}

/** JPEG bytes (or base64) → decoded bitmap, off the UI thread where supported. */
export async function decodeFrame(frame: Exclude<Frame, null>): Promise<ImageBitmap | HTMLImageElement> {
  if (typeof frame === 'string') {
    const img = new Image()
    img.src = 'data:image/jpeg;base64,' + frame
    await img.decode()
    return img
  }
  const blob = new Blob([frame], { type: 'image/jpeg' })
  if (typeof createImageBitmap === 'function') return createImageBitmap(blob)
  const img = new Image()
  const url = URL.createObjectURL(blob)
  try {
    img.src = url
    await img.decode()
  } finally {
    URL.revokeObjectURL(url)
  }
  return img
}

/** Base64 of a frame, for the native pop-out window binding. */
export function frameToBase64(frame: Frame): string {
  if (frame === null) return ''
  if (typeof frame === 'string') return frame
  const bytes = new Uint8Array(frame)
  let bin = ''
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK) as unknown as number[])
  }
  return btoa(bin)
}
