"""The page that shares the meeting's browser tab with Presentia (see tab_feed.py).

Served by the sidecar at /share. It asks the browser to share a tab (the
same picker as Meet's "Present → A tab"), then a Worker reads the shared
video with MediaStreamTrackProcessor and sends a few JPEG pictures a second
to /ws/tabfeed. Doing this in a Worker keeps it going while this page is a
background tab — it never needs to be looked at again.
"""

from __future__ import annotations

CSP = ("default-src 'none'; script-src 'unsafe-inline' blob:; worker-src blob:; "
       "connect-src ws://127.0.0.1:7788 ws://localhost:7788; style-src 'unsafe-inline'; img-src data:")

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Presentia · Share the meeting tab</title>
<style>
  :root { --bg:#f4f6fb; --card:#fff; --ink:#0f172a; --muted:#5b6475; --line:#e3e7ef;
          --accent:#4f46e5; --ok:#059669; --warn:#b45309; --err:#dc2626; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0d1117; --card:#161b22; --ink:#e6edf3; --muted:#9aa4b2; --line:#2a313c;
            --accent:#818cf8; --ok:#34d399; --warn:#fbbf24; --err:#f87171; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font: 15px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; }
  main { max-width: 560px; margin: 48px auto; padding: 0 16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:28px; }
  h1 { font-size: 21px; margin: 0 0 6px; }
  p { margin: 0 0 14px; color: var(--muted); }
  ol { margin: 0 0 18px; padding-left: 20px; color: var(--muted); }
  li { margin: 4px 0; }
  b { color: var(--ink); }
  button { font: inherit; font-weight: 600; border: 0; border-radius: 10px; padding: 11px 18px;
           cursor: pointer; background: var(--accent); color: #fff; }
  button.ghost { background: transparent; color: var(--ink); border: 1px solid var(--line); }
  button[hidden] { display: none; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  .status { margin-top: 18px; padding: 12px 14px; border-radius: 10px; border: 1px solid var(--line);
            display:flex; gap:10px; align-items:flex-start; }
  .dot { width:10px; height:10px; border-radius:50%; margin-top:6px; flex-shrink:0; background: var(--muted); }
  .status.ok .dot { background: var(--ok); box-shadow: 0 0 0 4px color-mix(in srgb, var(--ok) 25%, transparent); }
  .status.warn .dot { background: var(--warn); }
  .status.err .dot { background: var(--err); }
  .status strong { display:block; }
  .status span { color: var(--muted); font-size: 13.5px; }
  .note { margin-top: 12px; font-size: 13.5px; color: var(--warn); }
</style>
</head>
<body>
<main>
  <div class="card">
    <h1>Share the meeting tab with Presentia</h1>
    <p>Presentia watches the tab you share, so monitoring keeps going while you use other apps.
       Nothing is sent anywhere except to Presentia on this computer.</p>
    <ol>
      <li>Click <b>Choose the meeting tab</b>.</li>
      <li>In the browser's window, pick your <b>Google Meet</b> (or Zoom / Teams) tab, then <b>Share</b>.</li>
      <li>Leave this tab open. You can switch tabs and apps freely.</li>
    </ol>
    <div class="row">
      <button id="choose">Choose the meeting tab</button>
      <button id="stop" class="ghost" hidden>Stop sharing</button>
    </div>
    <div id="status" class="status"><div class="dot"></div>
      <div><strong id="s1">Not sharing yet</strong><span id="s2">Presentia is waiting for a tab.</span></div></div>
    <div id="note" class="note" hidden></div>
  </div>
</main>
<script>
(() => {
  const token = new URLSearchParams(location.search).get('t') || ''
  const WS_URL = `ws://${location.host}/ws/tabfeed?t=${encodeURIComponent(token)}`
  const FPS = 3, MAX_W = 1920, QUALITY = 0.8
  const $ = (id) => document.getElementById(id)
  let worker = null, track = null, fallback = null

  function show(kind, l1, l2) {
    $('status').className = 'status ' + kind
    $('s1').textContent = l1
    $('s2').textContent = l2 || ''
  }
  function note(text) { $('note').hidden = !text; $('note').textContent = text || '' }

  // Reads the shared tab and sends pictures. A Worker is not slowed down
  // when this page is in the background.
  const workerSrc = `
    let ws = null, url = '', hello = null, open = false, stopped = false
    let fps = 3, maxW = 1920, q = 0.8, last = 0, busy = false, sent = 0, canvas = null, ctx = null
    function connect() {
      ws = new WebSocket(url)
      ws.binaryType = 'arraybuffer'
      ws.onopen = () => { open = true; ws.send(JSON.stringify(hello)); postMessage({ type: 'ws', open: true }) }
      ws.onclose = (e) => {
        open = false
        postMessage({ type: 'ws', open: false, code: e.code })
        if (!stopped && e.code !== 4401) setTimeout(connect, 2000)
      }
      ws.onmessage = (e) => {
        try {
          const m = JSON.parse(e.data)
          if (m.type === 'stop') { stopped = true; postMessage({ type: 'stop' }) }
          if (m.type === 'rate' && m.fps) fps = m.fps
        } catch (err) { /* not for us */ }
      }
    }
    async function pump(readable) {
      const reader = readable.getReader()
      for (;;) {
        const { value: frame, done } = await reader.read()
        if (done) break
        const now = performance.now()
        if (!open || busy || stopped || now - last < 1000 / fps) { frame.close(); continue }
        last = now
        busy = true
        try {
          const k = Math.min(1, maxW / frame.displayWidth)
          const w = Math.round(frame.displayWidth * k), h = Math.round(frame.displayHeight * k)
          if (!canvas || canvas.width !== w || canvas.height !== h) {
            canvas = new OffscreenCanvas(w, h)
            ctx = canvas.getContext('2d')
          }
          ctx.drawImage(frame, 0, 0, w, h)
          frame.close()
          const buf = await (await canvas.convertToBlob({ type: 'image/jpeg', quality: q })).arrayBuffer()
          if (open && ws.bufferedAmount < 4e6) {
            ws.send(buf)
            sent++
            if (sent === 1 || sent % 30 === 0) postMessage({ type: 'sent', n: sent, w, h })
          }
        } catch (err) {
          try { frame.close() } catch (e) {}
          postMessage({ type: 'error', message: String(err) })
        }
        busy = false
      }
      postMessage({ type: 'done' })
    }
    onmessage = (e) => {
      const m = e.data
      if (m.type === 'start') {
        url = m.url; hello = m.hello; fps = m.fps; maxW = m.maxW; q = m.quality
        connect()
        pump(m.readable)
      } else if (m.type === 'ended') {
        stopped = true
        if (open) ws.send(JSON.stringify({ type: 'ended' }))
        setTimeout(() => { try { ws.close() } catch (err) {} }, 200)
      }
    }`

  function stopAll(sayEnded) {
    if (worker) { if (sayEnded) worker.postMessage({ type: 'ended' }); setTimeout(((w) => () => w.terminate())(worker), 500); worker = null }
    if (fallback) { fallback.stop(sayEnded); fallback = null }
    if (track) { const t = track; track = null; t.onended = null; t.stop() }
    $('stop').hidden = true
    $('choose').textContent = 'Choose the meeting tab'
  }

  // Browsers without MediaStreamTrackProcessor: draw from a <video> on a timer.
  // Slower in a background tab, so the page asks to stay visible.
  function startFallback(stream, hello) {
    const video = document.createElement('video')
    video.muted = true; video.playsInline = true; video.srcObject = stream; video.play()
    const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d')
    let ws = null, open = false, stopped = false, timer = null
    const connect = () => {
      ws = new WebSocket(WS_URL); ws.binaryType = 'arraybuffer'
      ws.onopen = () => { open = true; ws.send(JSON.stringify(hello)); onWorker({ data: { type: 'ws', open: true } }) }
      ws.onclose = (e) => { open = false; onWorker({ data: { type: 'ws', open: false, code: e.code } }); if (!stopped && e.code !== 4401) setTimeout(connect, 2000) }
      ws.onmessage = (e) => { try { if (JSON.parse(e.data).type === 'stop') onWorker({ data: { type: 'stop' } }) } catch (err) {} }
    }
    connect()
    let sent = 0
    timer = setInterval(() => {
      if (!open || !video.videoWidth) return
      const k = Math.min(1, MAX_W / video.videoWidth)
      canvas.width = Math.round(video.videoWidth * k); canvas.height = Math.round(video.videoHeight * k)
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
      canvas.toBlob(async (b) => {
        if (b && open) { ws.send(await b.arrayBuffer()); sent++; if (sent === 1) onWorker({ data: { type: 'sent', n: 1, w: canvas.width, h: canvas.height } }) }
      }, 'image/jpeg', QUALITY)
    }, 1000 / FPS)
    note('This browser cannot share in the background as well: keep this tab visible, or use Chrome, Edge or Brave.')
    return { stop(sayEnded) { stopped = true; clearInterval(timer); if (sayEnded && open) ws.send(JSON.stringify({ type: 'ended' })); setTimeout(() => { try { ws.close() } catch (err) {} }, 200) } }
  }

  let label = ''
  function onWorker(e) {
    const m = e.data
    if (m.type === 'ws' && !m.open) {
      if (m.code === 4401) show('err', 'This link has expired', 'Open the share link again from Presentia (Meeting Monitor → Browser tab).')
      else if (track) show('warn', 'Reconnecting to Presentia…', 'Is Presentia still running?')
    } else if (m.type === 'sent') {
      show('ok', 'Sharing with Presentia', `${label || 'The meeting tab'} · ${m.w}×${m.h}. You can switch to other tabs and apps.`)
    } else if (m.type === 'stop') {
      stopAll(false)
      show('', 'Sharing stopped by Presentia', 'Choose the tab again to keep monitoring.')
    } else if (m.type === 'error') {
      show('warn', 'A picture could not be sent', m.message)
    }
  }

  $('choose').onclick = async () => {
    note('')
    let stream
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { displaySurface: 'browser', frameRate: { ideal: 5, max: 10 } },
        audio: false,
        preferCurrentTab: false,
        selfBrowserSurface: 'exclude',
        surfaceSwitching: 'include',
        monitorTypeSurfaces: 'exclude',
      })
    } catch (err) {
      show('', 'Not sharing', err && err.name === 'NotAllowedError' ? 'No tab was chosen.' : String(err))
      return
    }
    stopAll(false)
    track = stream.getVideoTracks()[0]
    const settings = track.getSettings ? track.getSettings() : {}
    // Chromium names the track "web-contents-media-stream://…", not after the tab.
    label = /^web-contents-media-stream/.test(track.label || '') ? '' : (track.label || '')
    const hello = { type: 'hello', label, surface: settings.displaySurface || '' }
    if (settings.displaySurface && settings.displaySurface !== 'browser') {
      note('You shared a ' + (settings.displaySurface === 'monitor' ? 'whole screen' : 'window') +
           '. A covered window stops updating; share the meeting TAB instead for monitoring that keeps going in the background.')
    }
    show('warn', 'Connecting to Presentia…', label)
    if ('MediaStreamTrackProcessor' in window) {
      const proc = new MediaStreamTrackProcessor({ track })
      worker = new Worker(URL.createObjectURL(new Blob([workerSrc], { type: 'text/javascript' })))
      worker.onmessage = onWorker
      worker.postMessage({ type: 'start', readable: proc.readable, url: WS_URL, hello, fps: FPS, maxW: MAX_W, quality: QUALITY },
                         [proc.readable])
    } else {
      fallback = startFallback(stream, hello)
    }
    track.onended = () => {
      stopAll(true)
      show('', 'Sharing stopped', 'Choose the meeting tab again to keep monitoring.')
    }
    $('stop').hidden = false
    $('choose').textContent = 'Choose a different tab'
  }

  $('stop').onclick = () => {
    stopAll(true)
    show('', 'Sharing stopped', 'Choose the meeting tab again to keep monitoring.')
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
    $('choose').disabled = true
    show('err', 'This browser cannot share a tab', 'Open this link in Chrome, Edge or Brave — the browser your meeting is in.')
  }
  window.addEventListener('beforeunload', (e) => { if (track) { e.preventDefault(); e.returnValue = '' } })
})()
</script>
</body>
</html>
"""
