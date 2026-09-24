import React, { useId } from 'react'

/**
 * The iridescent Presentia mark: four rounded tiles whose inner corners frame a
 * four-point spark, filled with the pastel spectrum.
 *
 * The native floating bubble draws this same shape from PNGs rendered by
 * presentia-desktop/assets/gen_bubble_logo.py — keep the paths in step.
 */
export const LOGO_PATHS = [
  'M16,0 H184 Q200,0 200,16 V55 A115,115 0 0 1 85,170 H16 Q0,170 0,154 V16 Q0,0 16,0 Z',
  'M244,0 H412 Q428,0 428,16 V154 Q428,170 412,170 H343 A115,115 0 0 1 228,55 V16 Q228,0 244,0 Z',
  'M16,200 H85 A115,115 0 0 1 200,315 V618 Q200,644 178,644 Q170,644 162,636 L4,466 Q0,461 0,452 V216 Q0,200 16,200 Z',
  'M343,200 H412 Q428,200 428,216 V262 Q428,272 421,279 L270,428 Q260,438 248,436 Q228,432 228,410 V315 A115,115 0 0 1 343,200 Z',
]

export const SPECTRUM_STOPS: [number, string][] = [
  [0, '#97DEF0'],
  [0.25, '#EFEEC6'],
  [0.5, '#C888F9'],
  [0.75, '#CBB9F6'],
  [1, '#E5D5ED'],
]

interface PresentiaLogoProps {
  /** Rendered height in px; width follows the mark's 428:644 aspect. */
  height?: number
  /** Optional explicit width; when set, the mark stretches to fill it. */
  width?: number
  className?: string
  title?: string
}

export default function PresentiaLogo({ height = 24, width, className = '', title }: PresentiaLogoProps) {
  // Several logos can be on screen at once; each needs its own gradient id.
  const gradId = `presentia-spectrum-${useId().replace(/:/g, '')}`
  return (
    <svg
      className={`presentia-logo ${className}`}
      viewBox="0 0 428 644"
      height={height}
      width={width ?? (height * 428) / 644}
      preserveAspectRatio={width ? 'none' : undefined}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <defs>
        <linearGradient id={gradId} gradientUnits="userSpaceOnUse" x1="428" y1="0" x2="100" y2="644">
          {SPECTRUM_STOPS.map(([o, c]) => (
            <stop key={o} offset={o} stopColor={c} />
          ))}
        </linearGradient>
      </defs>
      {LOGO_PATHS.map((d) => (
        <path key={d} d={d} fill={`url(#${gradId})`} />
      ))}
    </svg>
  )
}
