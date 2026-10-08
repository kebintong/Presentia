// Connects the floating bubble to the Monitor page.
//
// The bubble's buttons (Screen area, Window, Start, Stop, Hide) arrive as
// Wails events. They used to work only while the Monitor page was open; now
// App listens for them all the time: when the Monitor page is mounted it
// handles them at once, otherwise App opens the Monitor page and the command
// waits here until the page is ready.
//
// The picked area or window is also kept here, so leaving the Monitor page
// and coming back (or a bubble command reopening it) does not forget it.

import { EventsOn } from '../wailsjs/runtime/runtime'

export type BubbleCmd = 'screen_area' | 'win_picker' | 'tab_share' | 'launch' | 'stop' | 'quit' | 'failed'

export const BUBBLE_CMDS: BubbleCmd[] = ['screen_area', 'win_picker', 'tab_share', 'launch', 'stop', 'quit', 'failed']

type Handler = (cmd: BubbleCmd) => void

let handler: Handler | null = null
let pending: BubbleCmd[] = []
let lastSource: unknown = null

/** The Monitor page registers while it is mounted; returns the unregister. */
export function setBubbleHandler(h: Handler): () => void {
  handler = h
  const queued = pending
  pending = []
  // Let the page finish mounting before running queued commands.
  if (queued.length) setTimeout(() => queued.forEach((c) => handler?.(c)), 0)
  return () => { if (handler === h) handler = null }
}

/** Routes a command: to the page if it is open, otherwise queues it and
 *  returns true so the caller opens the page. */
export function dispatchBubbleCmd(cmd: BubbleCmd): boolean {
  if (handler) {
    handler(cmd)
    return false
  }
  // These only matter to an open page.
  if (cmd === 'quit' || cmd === 'failed') return false
  pending.push(cmd)
  return true
}

/** Subscribes to every bubble event; `openMonitor` brings the page up. */
export function listenToBubble(openMonitor: () => void): () => void {
  const offs: Array<() => void> = []
  for (const cmd of BUBBLE_CMDS) {
    try {
      offs.push(EventsOn(`native:bubble:${cmd}`, () => {
        if (dispatchBubbleCmd(cmd)) openMonitor()
      }))
    } catch { /* not running inside the desktop shell */ }
  }
  return () => offs.forEach((off) => { try { off() } catch { /* ignore */ } })
}

/** Drops queued commands (e.g. no class is open to run them in). */
export function clearPendingBubbleCmds(): void { pending = [] }

export function rememberSource<T>(src: T | null): void { lastSource = src }
export function rememberedSource<T>(): T | null { return lastSource as T | null }

/** Tells the native bubble what will be watched (kind '', 'area', 'window' or 'tab'). */
export function tellBubbleSource(kind: string, label: string, detail: string, classTitle: string): void {
  try {
    (window as any)['go']?.['main']?.['App']?.['SetBubbleSource']?.(kind, label, detail, classTitle)?.catch?.(() => {})
  } catch { /* browser preview */ }
}

/** Test helper. */
export function _resetBubbleBridge(): void { handler = null; pending = []; lastSource = null }
