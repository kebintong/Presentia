import React from 'react'

type BannerLevel = 'info' | 'ok' | 'warn' | 'error'

interface StatusBannerProps {
  text: string
  level: BannerLevel
  spinner?: boolean
}

export default function StatusBanner({ text, level, spinner = false }: StatusBannerProps) {
  return (
    <div className={`banner banner-${level} flex items-center justify-center gap-2`}>
      {spinner && <span className="spinner" style={{ width: 16, height: 16 }} />}
      <span>{text}</span>
    </div>
  )
}
