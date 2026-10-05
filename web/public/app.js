// Presentia class registration: join code → details → live face check → submit.
import { Challenge, createTracker, takePhoto, PROMPTS } from '/liveness.js'

const $ = (id) => document.getElementById(id)
const STEPS = ['code', 'details', 'camera', 'review', 'done']

const state = {
  code: '',
  cls: null,          // { code, name, section }
  studentNo: '',
  name: '',
  photos: [],
  liveness: null,
}

// Local testing only: lets automated tests drive the face check without a
// real face. It changes nothing a direct API call couldn't already do, and
// the desktop app still checks every face itself.
const TEST = (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
  && new URLSearchParams(location.search).has('test')

// ── navigation ────────────────────────────────────────────────────────────

function show(step) {
  for (const s of document.querySelectorAll('.step')) s.hidden = s.dataset.step !== step
  const idx = STEPS.indexOf(step)
  for (const li of document.querySelectorAll('.progress li')) {
    const i = STEPS.indexOf(li.dataset.for)
    li.classList.toggle('current', i === idx)
    li.classList.toggle('done', i < idx)
  }
  document.querySelector('.progress').hidden = step === 'done'
  if (step !== 'camera') stopCamera()
  const first = document.querySelector(`.step[data-step="${step}"] h1`)
  first?.setAttribute('tabindex', '-1')
  first?.focus({ preventScroll: true })
  window.scrollTo({ top: 0 })
}

for (const btn of document.querySelectorAll('[data-go]')) {
  btn.addEventListener('click', () => show(btn.dataset.go))
}

function setError(id, message) {
  const el = $(id)
  el.textContent = message || ''
  el.hidden = !message
}

async function api(path, options = {}) {
  let res
  try {
    res = await fetch(path, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    })
  } catch {
    throw new Error('No connection. Check your internet and try again.')
  }
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.message || `Something went wrong (${res.status}).`)
  return body
}

// ── 1. join code ──────────────────────────────────────────────────────────

const codeInput = $('code')
const formatCode = (raw) => {
  const c = raw.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6)
  return c.length > 3 ? `${c.slice(0, 3)}-${c.slice(3)}` : c
}
codeInput.addEventListener('input', () => {
  codeInput.value = formatCode(codeInput.value)
  setError('code-error', '')
})

async function lookUp(code) {
  const btn = $('code-submit')
  btn.disabled = true
  btn.textContent = 'Checking…'
  try {
    state.cls = await api(`/api/classes/${encodeURIComponent(code)}`)
    state.code = state.cls.code
    $('class-name').textContent = state.cls.name
    $('class-section').textContent = state.cls.section || ''
    show('details')
    $('student-no').focus()
  } catch (err) {
    setError('code-error', err.message)
  } finally {
    btn.disabled = false
    btn.textContent = 'Continue'
  }
}

$('code-form').addEventListener('submit', (e) => {
  e.preventDefault()
  const code = codeInput.value.replace('-', '')
  if (code.length !== 6) {
    setError('code-error', 'A join code has 6 letters and numbers.')
    return
  }
  lookUp(code)
})

// ── 2. details ────────────────────────────────────────────────────────────

$('details-form').addEventListener('submit', (e) => {
  e.preventDefault()
  const no = $('student-no').value.trim()
  const name = $('full-name').value.replace(/\s+/g, ' ').trim()
  if (!/^[A-Za-z0-9][A-Za-z0-9 ._\-\/]{0,31}$/.test(no)) {
    setError('details-error', 'Enter your student ID number (letters, numbers and - . / only).')
    return
  }
  if (name.length < 2) {
    setError('details-error', 'Enter your full name.')
    return
  }
  if (!$('consent').checked) {
    setError('details-error', 'Tick the box to agree to the privacy notice.')
    return
  }
  setError('details-error', '')
  state.studentNo = no
  state.name = name
  show('camera')
})

// ── 3. face check ─────────────────────────────────────────────────────────

const video = $('video')
const canvas = document.createElement('canvas')
let stream = null
let tracker = null
let running = false
let challenge = null

function stopCamera() {
  running = false
  if (stream) {
    for (const t of stream.getTracks()) t.stop()
    stream = null
  }
  video.srcObject = null
  $('stage').classList.remove('live', 'ok', 'fail')
  $('stage-idle').hidden = false
}

function renderDots(ch) {
  const dots = $('dots')
  dots.innerHTML = ''
  ch.steps.forEach((s, i) => {
    const li = document.createElement('li')
    li.title = PROMPTS[s]
    if (i < ch.i || ch.result === 'passed') li.className = 'done'
    else if (i === ch.i) li.className = 'current'
    dots.appendChild(li)
  })
}

/** Fake tracker for automated tests: acts out each prompt. */
function testTracker(getChallenge) {
  const lm = (yaw = 0.5, ear = 0.3) => {
    const pts = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5 }))
    pts[234] = { x: 0.3, y: 0.5 }; pts[454] = { x: 0.7, y: 0.5 }
    pts[1] = { x: 0.3 + yaw * 0.4, y: 0.55 }
    const eye = (idx, x0) => {
      const v = ear * 0.1
      pts[idx[0]] = { x: x0, y: 0.4 }; pts[idx[3]] = { x: x0 + 0.1, y: 0.4 }
      pts[idx[1]] = { x: x0 + 0.03, y: 0.4 - v / 2 }; pts[idx[5]] = { x: x0 + 0.03, y: 0.4 + v / 2 }
      pts[idx[2]] = { x: x0 + 0.07, y: 0.4 - v / 2 }; pts[idx[4]] = { x: x0 + 0.07, y: 0.4 + v / 2 }
    }
    eye([33, 160, 158, 133, 153, 144], 0.35)
    eye([362, 385, 387, 263, 373, 380], 0.55)
    pts[10] = { x: 0.5, y: 0.2 }; pts[152] = { x: 0.5, y: 0.8 }
    return pts
  }
  let n = 0
  let since = 0
  let stage = null
  return {
    detect() {
      n++
      const ch = getChallenge()
      if (ch.stage !== stage) { stage = ch.stage; since = performance.now() }
      if (performance.now() - since < 700) return [lm()] // a moment to "read" the prompt
      if (stage === 'blink2') return [lm(0.5, n % 6 < 3 ? 0.1 : 0.3)]
      if (stage === 'turn_any') return [lm(0.2)]
      if (stage === 'turn_back') return [lm(0.8)]
      return [lm()]
    },
    close() {},
  }
}

async function startCheck() {
  setError('camera-error', '')
  const btn = $('camera-start')
  btn.disabled = true
  btn.textContent = 'Starting…'
  $('prompt').textContent = 'Starting the camera…'
  try {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('This browser cannot use the camera. Open this page in Chrome, Safari or Edge.')
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 960 }, height: { ideal: 720 } },
        audio: false,
      })
    } catch (err) {
      throw new Error(err?.name === 'NotAllowedError'
        ? 'Camera permission was blocked. Allow camera access for this site in your browser settings, then tap "Try again".'
        : 'No camera was found, or another app is using it. Close other apps that use the camera and try again.')
    }
    video.srcObject = stream
    await video.play()
    $('stage-idle').hidden = true
    $('stage').classList.add('live')

    challenge = new Challenge()
    if (!tracker) {
      $('prompt').textContent = 'Loading the face check… (first time only)'
      tracker = TEST ? testTracker(() => challenge) : await createTracker()
    }
    state.photos = []
    running = true
    $('tips').hidden = true
    btn.hidden = true
    renderDots(challenge)

    const loop = () => {
      if (!running) return
      const now = performance.now()
      if (video.readyState >= 2) {
        const faces = tracker.detect(video)
        const view = challenge.update(faces, now)
        if (view.wantPhoto && faces.length === 1) {
          state.photos.push(takePhoto(video, faces[0], canvas))
        }
        $('prompt').textContent = view.hint || view.prompt
        $('stage').classList.toggle('ok', !!faces.length && !view.hint)
        renderDots(challenge)
        if (challenge.result === 'passed') {
          running = false
          state.liveness = challenge.summary(now)
          $('stage').classList.add('ok')
          setTimeout(finishCheck, 500)
          return
        }
        if (challenge.result === 'failed') {
          running = false
          $('stage').classList.add('fail')
          btn.hidden = false
          btn.disabled = false
          btn.textContent = 'Try again'
          return
        }
      }
      requestAnimationFrame(loop)
    }
    requestAnimationFrame(loop)
  } catch (err) {
    stopCamera()
    $('prompt').textContent = ''
    setError('camera-error', err.message || 'The face check could not start.')
    btn.hidden = false
    btn.disabled = false
    btn.textContent = 'Try again'
  }
}

$('camera-start').addEventListener('click', () => {
  stopCamera()
  startCheck()
})

function finishCheck() {
  stopCamera()
  const shots = $('shots')
  shots.innerHTML = ''
  for (const p of state.photos) {
    const img = document.createElement('img')
    img.src = `data:image/jpeg;base64,${p}`
    img.alt = 'Face photo'
    shots.appendChild(img)
  }
  $('sum-class').textContent = state.cls.section ? `${state.cls.name} · ${state.cls.section}` : state.cls.name
  $('sum-no').textContent = state.studentNo
  $('sum-name').textContent = state.name
  setError('submit-error', '')
  show('review')
}

// ── 4. submit ─────────────────────────────────────────────────────────────

$('retake').addEventListener('click', () => {
  const btn = $('camera-start')
  btn.hidden = false
  btn.disabled = false
  btn.textContent = 'Start camera'
  $('tips').hidden = false
  $('prompt').textContent = ''
  $('dots').innerHTML = ''
  show('camera')
})

$('submit').addEventListener('click', async () => {
  const btn = $('submit')
  btn.disabled = true
  btn.textContent = 'Submitting…'
  setError('submit-error', '')
  try {
    await api('/api/registrations', {
      method: 'POST',
      body: JSON.stringify({
        code: state.code,
        student_no: state.studentNo,
        name: state.name,
        consent: true,
        photos: state.photos,
        liveness: state.liveness,
      }),
    })
    $('done-text').textContent =
      `${state.name}, you're registered for ${state.cls.name}. Your teacher will accept you in ` +
      'Presentia, and after that you are recognised automatically in class.'
    state.photos = []
    show('done')
  } catch (err) {
    setError('submit-error', err.message)
  } finally {
    btn.disabled = false
    btn.textContent = 'Submit registration'
  }
})

// ── start ─────────────────────────────────────────────────────────────────

const fromLink = new URLSearchParams(location.search).get('code')
show('code')
if (fromLink) {
  codeInput.value = formatCode(fromLink)
  if (codeInput.value.replace('-', '').length === 6) lookUp(codeInput.value.replace('-', ''))
}
