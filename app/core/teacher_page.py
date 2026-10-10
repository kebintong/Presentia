"""The teacher's pages in server mode (app/gateway.py): sign-in, and the
monitor page — choose a class, share the meeting tab, watch the roster.

Plain HTML and JavaScript, no build step. The tab is shared from this same
page (a Worker sends the pictures to /ws/feed, see share_page.WORKER_SRC) and
monitoring uses the engine's own messages over /ws/monitor.
"""

from __future__ import annotations

import html
import json

from app.core.share_page import WORKER_SRC

_STYLE = r"""
  :root { --bg:#f4f6fb; --card:#fff; --ink:#0f172a; --muted:#5b6475; --line:#e3e7ef; --sub:#f7f8fb;
          --accent:#4f46e5; --ok:#059669; --okbg:#d1fae5; --warn:#b45309; --warnbg:#fef3c7;
          --err:#dc2626; --errbg:#fee2e2; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0d1117; --card:#161b22; --ink:#e6edf3; --muted:#9aa4b2; --line:#2a313c; --sub:#1c222b;
            --accent:#818cf8; --ok:#34d399; --okbg:#0f2e25; --warn:#fbbf24; --warnbg:#33270d;
            --err:#f87171; --errbg:#3a1616; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font: 14.5px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; }
  button, input, select { font: inherit; }
  button { font-weight:600; border:0; border-radius:10px; padding:9px 16px; cursor:pointer;
           background:var(--accent); color:#fff; }
  button.ghost { background:transparent; color:var(--ink); border:1px solid var(--line); }
  button.danger { background:var(--err); }
  button:disabled { opacity:.5; cursor:default; }
  input, select { width:100%; padding:10px 12px; border-radius:10px; border:1px solid var(--line);
                  background:var(--sub); color:var(--ink); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:20px; }
  .muted { color:var(--muted); }
  .brand { display:flex; align-items:center; gap:10px; font-weight:700; font-size:17px; }
  .mark { width:30px; height:30px; border-radius:9px; background:var(--accent); color:#fff;
          display:grid; place-items:center; font-weight:800; }
"""

_LOGIN = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Presentia · Sign in</title><style>__STYLE__
  main { max-width:380px; margin:12vh auto; padding:0 16px; }
  h1 { font-size:20px; margin:18px 0 4px; }
  label { display:block; font-size:13px; font-weight:600; margin:14px 0 6px; }
  .error { margin-top:14px; padding:10px 12px; border-radius:10px; background:var(--errbg); color:var(--err); }
  form button { width:100%; margin-top:20px; padding:11px; }
</style></head><body><main>
  <div class="brand"><span class="mark">P</span>Presentia</div>
  <div class="card" style="margin-top:16px">
    <h1>Sign in</h1>
    <div class="muted">Use the account the Presentia administrator gave you.</div>
    __ERROR__
    <form method="post" action="/login" autocomplete="on">
      <label for="u">Username</label><input id="u" name="username" autocomplete="username" required autofocus>
      <label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button>
    </form>
  </div>
</main></body></html>
"""


def login_html(error: str = "") -> str:
    err = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return _LOGIN.replace("__STYLE__", _STYLE).replace("__ERROR__", err)


_APP = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Presentia · Monitor</title><style>__STYLE__
  header { display:flex; align-items:center; justify-content:space-between; gap:12px;
           padding:14px 20px; border-bottom:1px solid var(--line); background:var(--card); }
  header form { margin:0; }
  main { max-width:1180px; margin:20px auto; padding:0 16px; display:grid; gap:16px;
         grid-template-columns: 360px 1fr; align-items:start; }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  .col { display:grid; gap:16px; }
  h2 { font-size:12px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin:0 0 10px; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .status { display:flex; gap:10px; align-items:flex-start; padding:10px 12px; border-radius:10px;
            background:var(--sub); border:1px solid var(--line); margin-top:10px; font-size:13.5px; }
  .dot { width:9px; height:9px; border-radius:50%; background:var(--muted); margin-top:6px; flex-shrink:0; }
  .status.ok { border-color:var(--ok); } .status.ok .dot { background:var(--ok); }
  .status.warn { border-color:var(--warn); } .status.warn .dot { background:var(--warn); }
  .status.err { border-color:var(--err); } .status.err .dot { background:var(--err); }
  .stats { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:12px; }
  .stat { background:var(--sub); border:1px solid var(--line); border-radius:12px; padding:8px; text-align:center; }
  .stat b { display:block; font-size:20px; }
  .stat span { font-size:10.5px; font-weight:700; color:var(--muted); letter-spacing:.04em; }
  .roster { display:grid; gap:6px; max-height:420px; overflow:auto; }
  .student { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:8px 12px;
             border-radius:10px; background:var(--sub); border:1px solid var(--line); }
  .badge { font-size:11px; font-weight:700; padding:2px 8px; border-radius:999px; white-space:nowrap; }
  .b-present { background:var(--okbg); color:var(--ok); }
  .b-warn { background:var(--warnbg); color:var(--warn); }
  .b-off { background:var(--errbg); color:var(--err); }
  .b-wait { background:var(--sub); color:var(--muted); border:1px solid var(--line); }
  .faces { display:flex; flex-wrap:wrap; gap:8px; }
  .faces button { width:58px; height:58px; padding:0; overflow:hidden; border:2px solid var(--accent); background:var(--sub); }
  .faces img { width:100%; height:100%; object-fit:cover; display:block; }
  .live { width:100%; aspect-ratio:16/9; background:var(--sub); border-radius:12px; border:1px solid var(--line);
          display:grid; place-items:center; overflow:hidden; }
  .live img { width:100%; height:100%; object-fit:contain; display:block; }
  .log { display:grid; gap:4px; max-height:260px; overflow:auto; font-size:13px; }
  .log div { padding:6px 10px; border-radius:8px; background:var(--sub); }
  .log .warn { color:var(--warn); } .log .error { color:var(--err); } .log .ok { color:var(--ok); }
  dialog { border:1px solid var(--line); border-radius:16px; background:var(--card); color:var(--ink);
           width:min(380px, 92vw); padding:20px; }
  dialog::backdrop { background:rgba(0,0,0,.45); }
  .pick { display:grid; gap:4px; max-height:240px; overflow:auto; margin:10px 0; }
  .pick button { text-align:left; background:var(--sub); color:var(--ink); border:1px solid var(--line); font-weight:500; }
  .pick button.sel { border-color:var(--accent); }
  .note { padding:8px 10px; border-radius:10px; background:var(--warnbg); color:var(--warn); font-size:13px; }
</style></head><body>
<header>
  <div class="brand"><span class="mark">P</span>Presentia <span class="muted" style="font-weight:500" id="who"></span></div>
  <form method="post" action="/logout"><button class="ghost" type="submit">Sign out</button></form>
</header>
<main>
  <div class="col">
    <section class="card">
      <h2>1 · Class</h2>
      <select id="cls"></select>
      <div class="muted" id="clsNote" style="font-size:13px;margin-top:8px"></div>
    </section>
    <section class="card">
      <h2>2 · Share your meeting tab</h2>
      <div class="muted" style="font-size:13.5px">Pick your Google Meet (or Zoom / Teams) tab. Tip: join the meeting a
        second time in its own tab with the layout set to <b>Tiled, 49 tiles</b>, and share that tab.</div>
      <div class="row" style="margin-top:12px">
        <button id="share">Choose the meeting tab</button>
        <button id="unshare" class="ghost" hidden>Stop sharing</button>
      </div>
      <div class="status" id="shareStatus"><span class="dot"></span><span>Not sharing yet.</span></div>
    </section>
    <section class="card">
      <h2>3 · Monitor</h2>
      <div class="row">
        <button id="start">Start monitoring</button>
        <button id="stop" class="danger" hidden>Stop</button>
      </div>
      <div class="status" id="monStatus"><span class="dot"></span><span>Not monitoring.</span></div>
    </section>
    <section class="card">
      <h2>Activity</h2>
      <div class="log" id="log"><div class="muted">Nothing yet.</div></div>
    </section>
  </div>
  <div class="col">
    <section class="card">
      <h2>Students</h2>
      <div class="stats">
        <div class="stat"><b id="nHere">0</b><span>HERE</span></div>
        <div class="stat"><b id="nSeen">0</b><span>NOT SEEN</span></div>
        <div class="stat"><b id="nOff">0</b><span>CAMERA OFF</span></div>
        <div class="stat"><b id="nWait">0</b><span>NOT YET</span></div>
      </div>
      <div class="roster" id="roster"><div class="muted">Start monitoring to see your students.</div></div>
    </section>
    <section class="card" id="unkCard" hidden>
      <h2>Unknown faces — click to say who it is</h2>
      <div class="faces" id="faces"></div>
    </section>
    <section class="card">
      <div class="row" style="justify-content:space-between"><h2 style="margin:0">Live view</h2>
        <label class="muted" style="font-size:13px"><input type="checkbox" id="showLive" style="width:auto"> Show</label></div>
      <div class="live" id="live" hidden style="margin-top:10px"><span class="muted">Waiting for a picture…</span></div>
    </section>
  </div>
</main>
<dialog id="who_dlg">
  <b>Who is this?</b>
  <img id="whoImg" alt="" style="width:100%;height:140px;object-fit:cover;border-radius:10px;margin-top:10px">
  <div class="muted" style="font-size:13px;margin-top:8px">This face is added to the student's record, so they are recognised from now on.</div>
  <div class="pick" id="whoList"></div>
  <div class="note" id="whoCheck" hidden></div>
  <div class="row" style="justify-content:flex-end;margin-top:12px">
    <button class="ghost" id="whoCancel">Cancel</button>
    <button id="whoOk" disabled>This is them</button>
  </div>
</dialog>
<script>
(() => {
  const $ = (id) => document.getElementById(id)
  const wsBase = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host
  const LABEL = {
    present: ['PRESENT', 'b-present'], unclear: ['ON CAMERA', 'b-present'], unseen: ['NOT SEEN', 'b-warn'],
    cam_off: ['CAMERA OFF', 'b-off'], missing: ['MISSING', 'b-off'], waiting: ['NOT YET', 'b-wait'],
  }
  const PAUSED = { tab_waiting: 'Waiting for your shared tab', tab_stopped: 'Tab sharing stopped',
                   tab_stale: 'Your shared tab is not updating' }
  let me = null, mon = null, monitoring = false, roster = [], unknowns = [], liveUrl = null
  let track = null, worker = null, pick = null

  function status(el, kind, text) {
    el.className = 'status ' + (kind || '')
    el.innerHTML = '<span class="dot"></span><span></span>'
    el.lastChild.textContent = text
  }
  function log(text, level) {
    const box = $('log')
    if (box.firstChild && box.firstChild.classList.contains('muted')) box.innerHTML = ''
    const d = document.createElement('div')
    d.className = level || ''
    d.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + '  ' + text
    box.prepend(d)
    while (box.children.length > 80) box.lastChild.remove()
  }

  // ── who am I, which classes ─────────────────────────────────────────
  fetch('/api/me').then((r) => r.ok ? r.json() : Promise.reject(r)).then((d) => {
    me = d
    $('who').textContent = '· ' + d.name
    const sel = $('cls')
    sel.innerHTML = ''
    for (const c of d.classes) {
      const o = document.createElement('option')
      o.value = c.id
      o.textContent = c.name + (c.section ? ' · ' + c.section : '') + ' (' + c.students + ' students)'
      sel.append(o)
    }
    if (!d.classes.length) {
      $('clsNote').textContent = 'No classes are assigned to you yet. Ask the Presentia administrator.'
      $('start').disabled = true
    }
  }).catch(() => { location.href = '/login' })

  // ── sharing the meeting tab ─────────────────────────────────────────
  const workerSrc = __WORKER_SRC__
  function stopSharing(sayEnded) {
    if (worker) { if (sayEnded) worker.postMessage({ type: 'ended' }); const w = worker; setTimeout(() => w.terminate(), 500); worker = null }
    if (track) { const t = track; track = null; t.onended = null; t.stop() }
    $('unshare').hidden = true
    $('share').textContent = 'Choose the meeting tab'
  }
  $('share').onclick = async () => {
    let stream
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { displaySurface: 'browser', frameRate: { ideal: 5, max: 10 } }, audio: false,
        preferCurrentTab: false, selfBrowserSurface: 'exclude', surfaceSwitching: 'include', monitorTypeSurfaces: 'exclude',
      })
    } catch (e) { status($('shareStatus'), '', 'No tab was chosen.'); return }
    stopSharing(false)
    track = stream.getVideoTracks()[0]
    const settings = track.getSettings ? track.getSettings() : {}
    if (!('MediaStreamTrackProcessor' in window)) {
      status($('shareStatus'), 'err', 'This browser cannot share in the background. Use Chrome, Edge or Brave.')
      stopSharing(false); return
    }
    const proc = new MediaStreamTrackProcessor({ track })
    worker = new Worker(URL.createObjectURL(new Blob([workerSrc], { type: 'text/javascript' })))
    worker.onmessage = (e) => {
      const m = e.data
      if (m.type === 'sent') status($('shareStatus'), 'ok', 'Sharing your meeting tab (' + m.w + '×' + m.h + '). You can switch to other tabs and apps.')
      else if (m.type === 'ws' && !m.open && track) status($('shareStatus'), 'warn', 'Reconnecting to the Presentia server…')
      else if (m.type === 'stop') { stopSharing(false); status($('shareStatus'), '', 'Sharing stopped by the server.') }
    }
    worker.postMessage({ type: 'start', readable: proc.readable, url: wsBase + '/ws/feed',
                         hello: { type: 'hello', label: '', surface: settings.displaySurface || '' },
                         fps: 3, maxW: 1920, quality: 0.8 }, [proc.readable])
    track.onended = () => { stopSharing(true); status($('shareStatus'), '', 'Sharing stopped. Choose the tab again to keep monitoring.') }
    $('unshare').hidden = false
    $('share').textContent = 'Choose a different tab'
    status($('shareStatus'), 'warn', settings.displaySurface && settings.displaySurface !== 'browser'
      ? 'A window or screen is shared, not a tab — a covered window stops updating. Choose the tab instead.'
      : 'Connecting…')
  }
  $('unshare').onclick = () => { stopSharing(true); status($('shareStatus'), '', 'Not sharing.') }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
    $('share').disabled = true
    status($('shareStatus'), 'err', 'This browser cannot share a tab. Use Chrome, Edge or Brave on a computer.')
  }

  // ── monitoring ──────────────────────────────────────────────────────
  function render() {
    const n = { here: 0, seen: 0, off: 0, wait: 0 }
    const box = $('roster')
    box.innerHTML = ''
    for (const s of roster) {
      if (s.state === 'present' || s.state === 'unclear') n.here++
      else if (s.state === 'unseen') n.seen++
      else if (s.state === 'cam_off' || s.state === 'missing') n.off++
      else n.wait++
      const [label, cls] = LABEL[s.state] || LABEL.waiting
      const row = document.createElement('div')
      row.className = 'student'
      const nm = document.createElement('span'); nm.textContent = s.name
      const b = document.createElement('span'); b.className = 'badge ' + cls; b.textContent = label
      row.append(nm, b)
      box.append(row)
    }
    if (!roster.length) box.innerHTML = '<div class="muted">No students in this class yet.</div>'
    $('nHere').textContent = n.here; $('nSeen').textContent = n.seen; $('nOff').textContent = n.off; $('nWait').textContent = n.wait
    const faces = $('faces')
    faces.innerHTML = ''
    for (const u of unknowns) {
      const btn = document.createElement('button')
      btn.title = 'Say who this is'
      const img = document.createElement('img'); img.alt = 'Unknown face'; img.src = 'data:image/jpeg;base64,' + u.crop_jpeg
      btn.append(img)
      btn.onclick = () => openPick(u)
      faces.append(btn)
    }
    $('unkCard').hidden = !unknowns.length
  }

  function setMonitoring(on) {
    monitoring = on
    $('start').hidden = on; $('stop').hidden = !on; $('cls').disabled = on
  }

  $('start').onclick = () => {
    const classId = Number($('cls').value)
    if (!classId) return
    if (mon) try { mon.close() } catch (e) {}
    mon = new WebSocket(wsBase + '/ws/monitor')
    mon.binaryType = 'blob'
    status($('monStatus'), 'warn', 'Connecting…')
    mon.onopen = () => mon.send(JSON.stringify({ action: 'start', region: { tab: true }, class_id: classId, name: '' }))
    mon.onmessage = (ev) => {
      if (typeof ev.data !== 'string') {
        if (!$('showLive').checked) return
        if (liveUrl) URL.revokeObjectURL(liveUrl)
        liveUrl = URL.createObjectURL(ev.data)
        const live = $('live')
        if (!live.firstChild || live.firstChild.tagName !== 'IMG') { live.innerHTML = ''; live.append(document.createElement('img')) }
        live.firstChild.src = liveUrl
        return
      }
      let d; try { d = JSON.parse(ev.data) } catch (e) { return }
      if (d.type === 'started') { setMonitoring(true); status($('monStatus'), 'ok', 'Monitoring.'); log('Monitoring started.', 'ok') }
      else if (d.type === 'analysis') { roster = d.roster || []; unknowns = d.unknowns || []; render() }
      else if (d.type === 'capture') {
        if (PAUSED[d.state]) status($('monStatus'), 'warn', 'Paused: ' + PAUSED[d.state] + '. Nobody is penalised meanwhile.')
        else if (monitoring) status($('monStatus'), 'ok', 'Monitoring.')
      }
      else if (d.type === 'alert') log(d.message, d.level)
      else if (d.type === 'error') { log(d.message, 'error'); if (!monitoring) status($('monStatus'), 'err', d.message) }
      else if (d.type === 'assign_check') { $('whoCheck').hidden = false; $('whoCheck').textContent = d.message; $('whoOk').textContent = 'Add anyway'; $('whoOk').dataset.confirm = '1' }
      else if (d.type === 'assigned') { $('who_dlg').close(); pick = null }
      else if (d.type === 'stopped') { setMonitoring(false); status($('monStatus'), '', 'Stopped. Attendance was saved.'); log('Monitoring stopped. Attendance saved.', 'ok') }
    }
    mon.onclose = () => {
      if (monitoring) { status($('monStatus'), 'err', 'Lost the connection to the Presentia server. Press Start again.'); log('Connection lost.', 'error') }
      setMonitoring(false)
    }
  }
  $('stop').onclick = () => { if (mon && mon.readyState === 1) mon.send(JSON.stringify({ action: 'stop' })) }
  $('showLive').onchange = () => { $('live').hidden = !$('showLive').checked }

  // ── "who is this?" ──────────────────────────────────────────────────
  function openPick(u) {
    pick = { uid: u.uid, student: null }
    $('whoImg').src = 'data:image/jpeg;base64,' + u.crop_jpeg
    $('whoCheck').hidden = true; $('whoOk').textContent = 'This is them'; $('whoOk').disabled = true; delete $('whoOk').dataset.confirm
    const list = $('whoList'); list.innerHTML = ''
    for (const s of [...roster].sort((a, b) => a.name.localeCompare(b.name))) {
      const btn = document.createElement('button')
      btn.textContent = s.name
      btn.onclick = () => {
        for (const x of list.children) x.classList.remove('sel')
        btn.classList.add('sel'); pick.student = s.id; $('whoOk').disabled = false
        $('whoCheck').hidden = true; $('whoOk').textContent = 'This is them'; delete $('whoOk').dataset.confirm
      }
      list.append(btn)
    }
    $('who_dlg').showModal()
  }
  $('whoCancel').onclick = () => { $('who_dlg').close(); pick = null }
  $('whoOk').onclick = () => {
    if (!pick || pick.student == null || !mon || mon.readyState !== 1) return
    mon.send(JSON.stringify({ action: 'assign_unknown', uid: pick.uid, student_id: pick.student, confirm: !!$('whoOk').dataset.confirm }))
  }
  window.addEventListener('beforeunload', (e) => { if (monitoring || track) { e.preventDefault(); e.returnValue = '' } })
})()
</script>
</body></html>
"""


def app_html() -> str:
    return _APP.replace("__STYLE__", _STYLE).replace("__WORKER_SRC__", json.dumps(WORKER_SRC))
