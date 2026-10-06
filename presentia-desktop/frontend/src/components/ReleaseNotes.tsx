import React from 'react'

/**
 * Release notes from GitHub, shown the way they read on the release page.
 *
 * Only the teacher-facing part is shown: everything from "## Changes since"
 * (the list of commits) or "## Download" on is left out, as is the
 * "Edit this before publishing" reminder. A small subset of Markdown is
 * understood — headings, bullet lists, **bold**, *italic*, `code` — and
 * turned into React elements, so nothing in the notes is ever run as HTML.
 */

const CUT_AT = /^#{1,6}\s*(changes since|download)\b/i
const PLACEHOLDER = /^_?edit this before publishing/i

export function teacherNotes(body: string): string {
  const out: string[] = []
  for (const raw of (body || '').replace(/\r\n?/g, '\n').split('\n')) {
    const line = raw.trimEnd()
    if (CUT_AT.test(line.trim())) break
    if (PLACEHOLDER.test(line.trim())) continue
    out.push(line)
  }
  // The release page title already says "What's new": drop a leading heading
  // with that name, and blank lines at either end.
  while (out.length && !out[0].trim()) out.shift()
  if (out.length && /^#{1,6}\s*what'?s new\s*$/i.test(out[0].trim())) out.shift()
  while (out.length && !out[0].trim()) out.shift()
  while (out.length && !out[out.length - 1].trim()) out.pop()
  return out.join('\n')
}

// **bold**, __bold__, *italic*, _italic_, `code`
const INLINE = /(\*\*[^*]+\*\*|(?<!\w)__[^_]+__(?!\w)|`[^`]+`|\*[^*\s][^*]*\*|(?<!\w)_[^_\s][^_]*_(?!\w))/g

function inline(text: string, key: string): React.ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, i) => {
    const k = `${key}-${i}`
    if (/^(\*\*|__).+(\*\*|__)$/.test(part)) return <strong key={k}>{part.slice(2, -2)}</strong>
    if (/^`.+`$/.test(part)) return <code key={k}>{part.slice(1, -1)}</code>
    if (/^([*_]).+\1$/.test(part)) return <em key={k}>{part.slice(1, -1)}</em>
    return <React.Fragment key={k}>{part}</React.Fragment>
  })
}

export default function ReleaseNotes({ body, empty }: { body: string; empty?: string }) {
  const text = teacherNotes(body)
  if (!text) return empty ? <p className="release-notes-empty">{empty}</p> : null

  const blocks: React.ReactNode[] = []
  let list: string[] = []
  let para: string[] = []

  const flushList = () => {
    if (!list.length) return
    const k = `ul${blocks.length}`
    blocks.push(<ul key={k}>{list.map((item, i) => <li key={i}>{inline(item, `${k}-${i}`)}</li>)}</ul>)
    list = []
  }
  const flushPara = () => {
    if (!para.length) return
    const k = `p${blocks.length}`
    blocks.push(<p key={k}>{inline(para.join(' '), k)}</p>)
    para = []
  }

  for (const raw of text.split('\n')) {
    const line = raw.trim()
    const heading = /^#{1,6}\s+(.*)$/.exec(line)
    const bullet = /^[-*+]\s+(.*)$/.exec(line) || /^\d+[.)]\s+(.*)$/.exec(line)
    if (!line) {
      flushList(); flushPara()
    } else if (heading) {
      flushList(); flushPara()
      const k = `h${blocks.length}`
      blocks.push(<div key={k} className="release-notes-heading">{inline(heading[1], k)}</div>)
    } else if (bullet) {
      flushPara()
      list.push(bullet[1])
    } else if (list.length && /^\s{2,}/.test(raw)) {
      list[list.length - 1] += ` ${line}` // a wrapped bullet
    } else {
      flushList()
      para.push(line)
    }
  }
  flushList(); flushPara()

  return <div className="release-notes">{blocks}</div>
}
