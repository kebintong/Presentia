// Live face check for registration, in the student's browser.
//
// The same idea (and thresholds) as the Presentia desktop check-in: look at
// the camera, then blink and turn the head in a random order, then look at the
// camera again. Three photos are taken while the student faces the camera.
// A printed photo cannot blink or turn, and a recording does not know the order.
//
// Everything runs on the device; only the three photos are uploaded.

const EAR_CLOSED = 0.2   // eye aspect ratio below this counts as closed
const EAR_OPEN = 0.25    // must recover above this to finish a blink
const BLINK_WINDOW = 4000 // ms: both blinks must happen this close together
const YAW_LOW = 0.36     // nose position between the cheeks: turned one way …
const YAW_HIGH = 0.64    // … or the other
const CENTER_LO = 0.42
const CENTER_HI = 0.58
const HOLD_MS = 350      // a pose must be held this long to count
const STEP_MS = 12000    // time allowed per action
const TOTAL_MS = 90000   // whole check
const MIN_FACE = 0.22    // face width as a share of the frame for good photos
const PHOTO_GAP = 700    // ms between the two closing photos

const LEFT_EYE = [33, 160, 158, 133, 153, 144]
const RIGHT_EYE = [362, 385, 387, 263, 373, 380]
const NOSE = 1
const LEFT_CHEEK = 234
const RIGHT_CHEEK = 454

export const PROMPTS = {
  center: 'Look straight at the camera',
  blink2: 'Blink twice',
  turn_any: 'Turn your head to one side',
  turn_back: 'Now turn to the other side',
  center_final: 'Look straight at the camera again',
}

function dist(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

function ear(lm, idx) {
  const p = idx.map((i) => lm[i])
  const h = dist(p[0], p[3])
  return h === 0 ? 1 : (dist(p[1], p[5]) + dist(p[2], p[4])) / (2 * h)
}

function yaw(lm) {
  const l = lm[LEFT_CHEEK].x
  const r = lm[RIGHT_CHEEK].x
  return r - l === 0 ? 0.5 : (lm[NOSE].x - l) / (r - l)
}

function faceBox(lm) {
  let x1 = 1, y1 = 1, x2 = 0, y2 = 0
  for (const p of lm) {
    if (p.x < x1) x1 = p.x
    if (p.y < y1) y1 = p.y
    if (p.x > x2) x2 = p.x
    if (p.y > y2) y2 = p.y
  }
  return { x1, y1, x2, y2 }
}

/** Load MediaPipe Face Landmarker from this site's own /vendor folder. */
export async function createTracker() {
  const { FilesetResolver, FaceLandmarker } = await import('/vendor/vision_bundle.mjs')
  const files = await FilesetResolver.forVisionTasks('/vendor/wasm')
  const make = (delegate) => FaceLandmarker.createFromOptions(files, {
    baseOptions: { modelAssetPath: '/vendor/face_landmarker.task', delegate },
    runningMode: 'VIDEO',
    numFaces: 2,
    minFaceDetectionConfidence: 0.5,
    minTrackingConfidence: 0.5,
  })
  let lmk
  try {
    lmk = await make('GPU')
  } catch {
    lmk = await make('CPU') // older phones, blocked WebGL
  }
  let last = -1
  return {
    /** All faces in the current video frame (arrays of landmarks). */
    detect(video) {
      let ts = performance.now()
      if (ts <= last) ts = last + 1
      last = ts
      return lmk.detectForVideo(video, ts).faceLandmarks || []
    },
    close() { lmk.close() },
  }
}

function shuffle(a) {
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

/**
 * The challenge itself: feed it the faces found in each frame with
 * `update(faces, now)`; it returns what to show and asks for a photo with
 * `wantPhoto` when the moment is right.
 */
export class Challenge {
  constructor() {
    const actions = shuffle([['blink2'], ['turn_any', 'turn_back']]).flat()
    this.steps = ['center', ...actions, 'center_final']
    this.i = 0
    this.started = null
    this.stepStarted = null
    this.holdSince = null
    this.holdSide = null
    this.firstSide = null
    this.blinks = []
    this.eyeClosed = false
    this.photosTaken = 0
    this.lastPhotoAt = 0
    this.result = null // 'passed' | 'failed'
    this.reason = ''
  }

  get stage() { return this.steps[this.i] }

  /** One frame. Returns {prompt, hint, ok, wantPhoto}. */
  update(faces, now) {
    if (this.result) return { prompt: this.reason, hint: '', ok: this.result === 'passed', wantPhoto: false }
    if (this.started === null) this.started = now
    if (this.stepStarted === null) this.stepStarted = now
    if (now - this.started > TOTAL_MS) return this._fail('Time is up. Tap "Try again" to start over.')
    const timed = this.stage !== 'center' && this.stage !== 'center_final'
    if (timed && now - this.stepStarted > STEP_MS) {
      return this._fail(`"${PROMPTS[this.stage]}" was not done in time. Tap "Try again".`)
    }

    if (faces.length === 0) return this._show('Move your face into the oval')
    if (faces.length > 1) return this._show('Only you should be in the picture')
    const lm = faces[0]
    const box = faceBox(lm)
    const y = yaw(lm)
    const centred = y > CENTER_LO && y < CENTER_HI
    const e = Math.min(ear(lm, LEFT_EYE), ear(lm, RIGHT_EYE))

    switch (this.stage) {
      case 'center':
        if (box.x2 - box.x1 < MIN_FACE) return this._show('Move a little closer')
        if (this._held(centred && e > EAR_OPEN, now)) {
          this._next(now)
          return { ...this._view(), wantPhoto: this._photo(now) }
        }
        break
      case 'blink2':
        if (!this.eyeClosed && e < EAR_CLOSED) {
          this.eyeClosed = true
        } else if (this.eyeClosed && e > EAR_OPEN) {
          this.eyeClosed = false
          this.blinks = [...this.blinks.filter((t) => now - t <= BLINK_WINDOW), now]
          if (this.blinks.length >= 2) this._next(now)
        }
        break
      case 'turn_any': {
        const side = y < YAW_LOW ? 'low' : y > YAW_HIGH ? 'high' : null
        if (this._held(side !== null, now, side)) {
          this.firstSide = side
          this._next(now)
        }
        break
      }
      case 'turn_back': {
        const other = this.firstSide === 'low' ? y > YAW_HIGH : y < YAW_LOW
        if (this._held(other, now)) this._next(now)
        break
      }
      case 'center_final':
        if (box.x2 - box.x1 < MIN_FACE) return this._show('Move a little closer')
        if (centred && e > EAR_OPEN && this.photosTaken < 3 && now - this.lastPhotoAt >= PHOTO_GAP) {
          const want = this._held(true, now)
          if (want) {
            const shot = this._photo(now)
            if (this.photosTaken >= 3) {
              this.result = 'passed'
              this.reason = 'Done!'
            }
            return { ...this._view(), wantPhoto: shot }
          }
        } else if (!centred || e <= EAR_OPEN) {
          this.holdSince = null
        }
        break
    }
    return this._view()
  }

  _photo(now) {
    this.photosTaken += 1
    this.lastPhotoAt = now
    this.holdSince = null
    return true
  }

  _held(cond, now, side = null) {
    if (cond && (side === null || side === this.holdSide || this.holdSince === null)) {
      if (this.holdSince === null) this.holdSince = now
      this.holdSide = side
    } else {
      this.holdSince = cond ? now : null
      this.holdSide = cond ? side : null
    }
    return this.holdSince !== null && now - this.holdSince >= HOLD_MS
  }

  _next(now) {
    this.i += 1
    this.stepStarted = now
    this.holdSince = null
    this.holdSide = null
    this.blinks = []
    this.eyeClosed = false
  }

  _fail(reason) {
    this.result = 'failed'
    this.reason = reason
    return { prompt: reason, hint: '', ok: false, wantPhoto: false }
  }

  _show(hint) {
    return { ...this._view(), hint }
  }

  _view() {
    let prompt = PROMPTS[this.stage]
    if (this.stage === 'blink2') prompt += ` (${this.blinks.length}/2)`
    return { prompt, hint: '', ok: false, wantPhoto: false }
  }

  /** Summary sent with the registration (the server only stores it). */
  summary(now) {
    return {
      steps: this.steps,
      seconds: this.started === null ? null : (now - this.started) / 1000,
      passed: this.result === 'passed',
    }
  }
}

/** Square crop around the face, 480 px, as a base64 JPEG (no data: prefix). */
export function takePhoto(video, face, canvas) {
  const vw = video.videoWidth
  const vh = video.videoHeight
  const b = faceBox(face)
  const cx = ((b.x1 + b.x2) / 2) * vw
  const cy = ((b.y1 + b.y2) / 2) * vh
  // Face takes about half the photo: enough context for the desktop's detector.
  const side = Math.min(Math.max((b.x2 - b.x1) * vw, (b.y2 - b.y1) * vh) * 2.0, vw, vh)
  const sx = Math.min(Math.max(0, cx - side / 2), vw - side)
  const sy = Math.min(Math.max(0, cy - side / 2), vh - side)
  canvas.width = 480
  canvas.height = 480
  const ctx = canvas.getContext('2d')
  ctx.drawImage(video, sx, sy, side, side, 0, 0, 480, 480)
  return canvas.toDataURL('image/jpeg', 0.88).replace(/^data:image\/jpeg;base64,/, '')
}
