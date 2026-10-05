/**
 * Presentia online registration — Cloudflare Worker (free plan).
 *
 * Serves the registration website (static files in /public) and a small
 * JSON API backed by D1:
 *
 *   Public (the website)
 *     GET    /api/classes/:code                 → class name for a join code
 *     POST   /api/registrations                 → submit details + 3 selfies
 *
 *   Host (the Presentia desktop app; Authorization: Bearer <id>.<secret>)
 *     POST   /api/host/register                 → create host credentials
 *     PUT    /api/host/classes/:code            → publish / update a class
 *     DELETE /api/host/classes/:code            → stop accepting, delete its data
 *     GET    /api/host/classes/:code/registrations?limit=10
 *     DELETE /api/host/registrations/:id        → after the app has saved it
 *
 * Privacy by design: the server never computes or keeps face templates. The
 * desktop app downloads the photos, builds the template locally and deletes
 * the registration here. A daily cron deletes anything left after
 * RETENTION_DAYS.
 */

const VERSION = '1.2.0'
const RETENTION_DAYS = 14
const REPORT_DAYS = 30             // diagnostic reports are kept this long
const REPORT_MAX = 200_000          // characters per diagnostic report
const ISSUES_PER_DAY = 50           // GitHub issues opened from reports, all senders together

const CODE_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{6}$/
const STUDENT_NO_RE = /^[A-Za-z0-9][A-Za-z0-9 ._\-\/]{0,31}$/
const MAX_BODY = 1_500_000          // bytes, whole registration request
const MAX_PHOTO = 350_000           // bytes per decoded JPEG
const MAX_PHOTOS = 3
const MAX_PENDING_PER_CLASS = 500

// Fixed-window limits per client IP (hashed): [count, seconds]. Generous on
// purpose: a whole class on campus Wi-Fi usually shares one public IP.
const LIMITS = {
  lookup: [300, 600],     // join-code lookups (guessing one of ~887 million codes stays hopeless)
  register: [120, 3600],  // registrations submitted
  host: [30, 3600],       // new host credentials (one per install; a staff training can share an IP)
  report: [20, 3600],     // diagnostic reports from the desktop app, per network
}

// ── small helpers ────────────────────────────────────────────────────────

const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Cache-Control': 'no-store',
}

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...SECURITY_HEADERS, ...headers },
  })
}

function fail(status, error, message) {
  return json({ error, message }, status)
}

const nowIso = () => new Date().toISOString()

function randomHex(bytes) {
  const a = new Uint8Array(bytes)
  crypto.getRandomValues(a)
  return [...a].map((b) => b.toString(16).padStart(2, '0')).join('')
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** Equal-time comparison of two hex strings of the same length. */
function sameHex(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false
  let diff = 0
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i)
  return diff === 0
}

function normalizeCode(raw) {
  return String(raw || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
}

function cleanName(raw) {
  return String(raw || '')
    .replace(/[\u0000-\u001f\u007f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Rough decoded size of a base64 string. */
function b64Bytes(s) {
  const pad = s.endsWith('==') ? 2 : s.endsWith('=') ? 1 : 0
  return Math.floor((s.length * 3) / 4) - pad
}

async function clientKey(request, purpose) {
  const ip = request.headers.get('CF-Connecting-IP') || 'local'
  return `${purpose}:${(await sha256Hex('presentia:' + ip)).slice(0, 32)}`
}

/** True when the caller is still within the limit for `purpose`. */
/** A limit shared by everyone (not per network), e.g. GitHub issues per day. */
async function allowGlobal(env, key, limit, windowSec) {
  const now = Math.floor(Date.now() / 1000)
  const row = await env.DB.prepare(
    `INSERT INTO rate_limits (key, count, window_start) VALUES (?1, 1, ?2)
     ON CONFLICT(key) DO UPDATE SET
       count        = CASE WHEN window_start <= ?2 - ?3 THEN 1  ELSE count + 1   END,
       window_start = CASE WHEN window_start <= ?2 - ?3 THEN ?2 ELSE window_start END
     RETURNING count`
  ).bind(`global:${key}`, now, windowSec).first()
  return !row || row.count <= limit
}

async function allow(env, request, purpose) {
  const [limit, windowSec] = LIMITS[purpose]
  const key = await clientKey(request, purpose)
  const now = Math.floor(Date.now() / 1000)
  const row = await env.DB.prepare(
    `INSERT INTO rate_limits (key, count, window_start) VALUES (?1, 1, ?2)
     ON CONFLICT(key) DO UPDATE SET
       count        = CASE WHEN window_start <= ?2 - ?3 THEN 1  ELSE count + 1   END,
       window_start = CASE WHEN window_start <= ?2 - ?3 THEN ?2 ELSE window_start END
     RETURNING count`
  ).bind(key, now, windowSec).first()
  return (row?.count ?? 1) <= limit
}

async function readJson(request, maxBytes) {
  const declared = Number(request.headers.get('Content-Length') || 0)
  if (declared > maxBytes) return { error: fail(413, 'too_large', 'The upload is too large.') }
  const text = await request.text()
  if (text.length > maxBytes) return { error: fail(413, 'too_large', 'The upload is too large.') }
  try {
    return { body: JSON.parse(text) }
  } catch {
    return { error: fail(400, 'bad_json', 'The request could not be read.') }
  }
}

/** The host making the request, or null. */
async function authHost(env, request) {
  const header = request.headers.get('Authorization') || ''
  const m = header.match(/^Bearer ([0-9a-f]{32})\.([0-9a-f]{64})$/)
  if (!m) return null
  const host = await env.DB.prepare('SELECT id, secret_hash FROM hosts WHERE id = ?1').bind(m[1]).first()
  if (!host || !sameHex(host.secret_hash, await sha256Hex(m[2]))) return null
  await env.DB.prepare('UPDATE hosts SET last_seen_at = ?1 WHERE id = ?2').bind(nowIso(), host.id).run()
  return host
}

// ── public API ───────────────────────────────────────────────────────────

async function getClass(env, request, rawCode) {
  if (!(await allow(env, request, 'lookup'))) {
    return fail(429, 'rate_limited', 'Too many tries. Wait a few minutes and try again.')
  }
  const code = normalizeCode(rawCode)
  if (!CODE_RE.test(code)) return fail(404, 'not_found', 'That join code does not exist.')
  const cls = await env.DB.prepare('SELECT code, name, section, open FROM classes WHERE code = ?1')
    .bind(code).first()
  if (!cls) return fail(404, 'not_found', 'That join code does not exist. Check it with your teacher.')
  if (!cls.open) return fail(403, 'closed', 'This class is not accepting registrations right now.')
  return json({ code: cls.code, name: cls.name, section: cls.section })
}

/** Adds registrations.device_hash to databases created before it existed.
 *  Runs once per Worker instance; "duplicate column" means it is there. */
let deviceColumnReady = false

async function ensureDeviceColumn(env) {
  if (deviceColumnReady) return
  try {
    await env.DB.prepare('ALTER TABLE registrations ADD COLUMN device_hash TEXT').run()
  } catch (err) {
    if (!/duplicate column/i.test(String(err && err.message))) throw err
  }
  deviceColumnReady = true
}

async function submitRegistration(env, request) {
  const { body, error } = await readJson(request, MAX_BODY)
  if (error) return error
  if (!(await allow(env, request, 'register'))) {
    return fail(429, 'rate_limited', 'Too many registrations from this connection. Try again later.')
  }

  const code = normalizeCode(body.code)
  const studentNo = cleanName(body.student_no)
  const name = cleanName(body.name)
  const photos = Array.isArray(body.photos) ? body.photos : []

  if (!CODE_RE.test(code)) return fail(400, 'bad_code', 'The join code is not valid.')
  if (!STUDENT_NO_RE.test(studentNo)) {
    return fail(400, 'bad_student_no', 'Enter your student ID number (letters, numbers and - . / only).')
  }
  if (name.length < 2 || name.length > 80) return fail(400, 'bad_name', 'Enter your full name.')
  if (body.consent !== true) {
    return fail(400, 'no_consent', 'You need to agree to the privacy notice to register.')
  }
  if (photos.length < 1 || photos.length > MAX_PHOTOS) {
    return fail(400, 'bad_photos', 'Take the face photos again.')
  }
  const cleaned = []
  for (const p of photos) {
    const b64 = String(p || '').replace(/^data:image\/jpeg;base64,/, '')
    // Every JPEG starts with FF D8 FF, which is "/9j/" in base64.
    if (!b64.startsWith('/9j/') || !/^[A-Za-z0-9+/]+={0,2}$/.test(b64)) {
      return fail(400, 'bad_photos', 'The photos could not be read. Take them again.')
    }
    if (b64Bytes(b64) > MAX_PHOTO) return fail(413, 'too_large', 'A photo is too large.')
    cleaned.push(b64)
  }

  const cls = await env.DB.prepare('SELECT code, open FROM classes WHERE code = ?1').bind(code).first()
  if (!cls) return fail(404, 'not_found', 'That join code does not exist.')
  if (!cls.open) return fail(403, 'closed', 'This class is not accepting registrations right now.')

  const pending = await env.DB.prepare('SELECT COUNT(*) AS n FROM registrations WHERE class_code = ?1')
    .bind(code).first()
  if ((pending?.n ?? 0) >= MAX_PENDING_PER_CLASS) {
    return fail(503, 'full', 'Your teacher has too many registrations waiting. Try again later.')
  }

  // Liveness summary from the browser, kept small and as plain data only.
  const live = body.liveness && typeof body.liveness === 'object' ? body.liveness : {}
  const liveness = JSON.stringify({
    steps: Array.isArray(live.steps) ? live.steps.slice(0, 8).map((s) => String(s).slice(0, 24)) : [],
    seconds: Number.isFinite(live.seconds) ? Math.round(live.seconds) : null,
    passed: live.passed === true,
  })

  const id = randomHex(16)
  const now = nowIso()
  // The browser sends a random ID it keeps (localStorage); only its hash is
  // stored. Submitting again from the SAME phone or browser replaces the
  // earlier, uncollected submission (a retake). Someone else using a student
  // ID that is already waiting is refused instead of overwriting it.
  const device = String(body.device || '')
  if (!/^[0-9a-f]{32}$/.test(device)) {
    return fail(400, 'old_page', 'Reload this page and try again.')
  }
  const deviceHash = await sha256Hex(`device:${device}`)
  await ensureDeviceColumn(env)
  const old = await env.DB.prepare(
    'SELECT id, device_hash FROM registrations WHERE class_code = ?1 AND student_no = ?2'
  ).bind(code, studentNo).first()
  if (old && old.device_hash && !sameHex(old.device_hash, deviceHash)) {
    return fail(409, 'number_waiting',
      `Student ID ${studentNo} already has a registration waiting for your teacher in this class, ` +
      'sent from another phone or computer. Check that you typed your own student ID. If it is yours ' +
      'and you did not register before, tell your teacher so they can reject the other one.')
  }
  const stmts = []
  if (old) {
    stmts.push(env.DB.prepare('DELETE FROM photos WHERE registration_id = ?1').bind(old.id))
    stmts.push(env.DB.prepare('DELETE FROM registrations WHERE id = ?1').bind(old.id))
  }
  stmts.push(env.DB.prepare(
    `INSERT INTO registrations (id, class_code, student_no, name, consent_at, liveness, created_at, device_hash)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)`
  ).bind(id, code, studentNo, name, now, liveness, now, deviceHash))
  cleaned.forEach((data, idx) => {
    stmts.push(env.DB.prepare('INSERT INTO photos (registration_id, idx, data) VALUES (?1, ?2, ?3)')
      .bind(id, idx, data))
  })
  await env.DB.batch(stmts)
  return json({ ok: true, replaced: !!old }, 201)
}

// ── host API (desktop app) ───────────────────────────────────────────────

async function registerHost(env, request) {
  if (!(await allow(env, request, 'host'))) return fail(429, 'rate_limited', 'Too many requests.')
  const id = randomHex(16)
  const secret = randomHex(32)
  await env.DB.prepare('INSERT INTO hosts (id, secret_hash, created_at) VALUES (?1, ?2, ?3)')
    .bind(id, await sha256Hex(secret), nowIso()).run()
  return json({ host_id: id, secret }, 201)
}

async function publishClass(env, request, host, rawCode) {
  const code = normalizeCode(rawCode)
  if (!CODE_RE.test(code)) return fail(400, 'bad_code', 'Invalid join code.')
  const { body, error } = await readJson(request, 10_000)
  if (error) return error
  const name = cleanName(body.name).slice(0, 80)
  const section = cleanName(body.section).slice(0, 80)
  if (!name) return fail(400, 'bad_name', 'The class needs a name.')
  const open = body.open === false ? 0 : 1

  const existing = await env.DB.prepare('SELECT host_id FROM classes WHERE code = ?1').bind(code).first()
  if (existing && existing.host_id !== host.id) {
    // Join codes are made on the desktop; a clash with another teacher's
    // class is rare, and the app simply picks a new code.
    return fail(409, 'code_taken', 'This join code is already used by another class.')
  }
  await env.DB.prepare(
    `INSERT INTO classes (code, host_id, name, section, open, updated_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6)
     ON CONFLICT(code) DO UPDATE SET name = ?3, section = ?4, open = ?5, updated_at = ?6`
  ).bind(code, host.id, name, section, open, nowIso()).run()
  return json({ code, name, section, open: !!open })
}

async function ownedClass(env, host, code) {
  return env.DB.prepare('SELECT code FROM classes WHERE code = ?1 AND host_id = ?2')
    .bind(code, host.id).first()
}

async function unpublishClass(env, host, rawCode) {
  const code = normalizeCode(rawCode)
  if (!(await ownedClass(env, host, code))) return json({ ok: true, existed: false })
  await env.DB.batch([
    env.DB.prepare(
      'DELETE FROM photos WHERE registration_id IN (SELECT id FROM registrations WHERE class_code = ?1)'
    ).bind(code),
    env.DB.prepare('DELETE FROM registrations WHERE class_code = ?1').bind(code),
    env.DB.prepare('DELETE FROM classes WHERE code = ?1').bind(code),
  ])
  return json({ ok: true, existed: true })
}

async function listRegistrations(env, host, rawCode, url) {
  const code = normalizeCode(rawCode)
  if (!(await ownedClass(env, host, code))) return fail(404, 'not_found', 'Class not found.')
  const limit = Math.min(20, Math.max(1, Number(url.searchParams.get('limit')) || 10))
  const { results: regs } = await env.DB.prepare(
    `SELECT id, student_no, name, consent_at, liveness, created_at
     FROM registrations WHERE class_code = ?1 ORDER BY created_at LIMIT ?2`
  ).bind(code, limit).all()
  const total = await env.DB.prepare('SELECT COUNT(*) AS n FROM registrations WHERE class_code = ?1')
    .bind(code).first()
  if (regs.length === 0) return json({ registrations: [], remaining: 0 })

  const marks = regs.map((_, i) => `?${i + 1}`).join(', ')
  const { results: photos } = await env.DB.prepare(
    `SELECT registration_id, idx, data FROM photos WHERE registration_id IN (${marks}) ORDER BY idx`
  ).bind(...regs.map((r) => r.id)).all()
  const byReg = {}
  for (const p of photos) (byReg[p.registration_id] ||= []).push(p.data)
  return json({
    registrations: regs.map((r) => ({
      ...r,
      liveness: (() => { try { return JSON.parse(r.liveness) } catch { return {} } })(),
      photos: byReg[r.id] || [],
    })),
    remaining: Math.max(0, (total?.n ?? 0) - regs.length),
  })
}

async function deleteRegistration(env, host, id) {
  if (!/^[0-9a-f]{32}$/.test(id)) return fail(404, 'not_found', 'Not found.')
  const reg = await env.DB.prepare(
    `SELECT r.id FROM registrations r JOIN classes c ON c.code = r.class_code
     WHERE r.id = ?1 AND c.host_id = ?2`
  ).bind(id, host.id).first()
  if (!reg) return json({ ok: true, existed: false })
  await env.DB.batch([
    env.DB.prepare('DELETE FROM photos WHERE registration_id = ?1').bind(id),
    env.DB.prepare('DELETE FROM registrations WHERE id = ?1').bind(id),
  ])
  return json({ ok: true, existed: true })
}

// ── diagnostic reports (desktop app → Settings → Diagnostics) ───────────
//
// POST /api/reports. The desktop app sends reports to a fixed address of its
// own (REPORTS_URL in app/data/cloud.py), not to whatever registration
// website it is set to, so no login is involved: the endpoint is public,
// checks the app header, and is limited per network and in size.
// Stored in D1 for REPORT_DAYS days. Read them in the Cloudflare dashboard:
//   SELECT id, created_at, app_version, summary FROM reports ORDER BY created_at DESC;
// Optionally also opened as a GitHub issue: set the secrets GITHUB_TOKEN
// (fine-grained token, Issues: read and write, on one repo only) and
// REPORTS_REPO ("owner/repo"). The repo MUST be private — reports can contain
// student names — so the Worker checks that and skips public repos.

let reportsTableReady = false

async function ensureReportsTable(env) {
  if (reportsTableReady) return
  await env.DB.prepare(
    `CREATE TABLE IF NOT EXISTS reports (
       id          TEXT PRIMARY KEY,
       host_id     TEXT,             -- the sending install's random ID
       created_at  TEXT NOT NULL,
       app_version TEXT NOT NULL DEFAULT '',
       summary     TEXT NOT NULL DEFAULT '',
       body        TEXT NOT NULL,
       issue_url   TEXT
     )`
  ).run()
  reportsTableReady = true
}

function oneLine(raw, max) {
  return cleanName(raw).slice(0, max)
}

async function submitReport(env, request, ctx, host = null) {
  // `host` is set for the older route (desktop app 1.5.2 sends to
  // /api/host/reports with its website credentials).
  if (!host && request.headers.get('X-Presentia-App') !== 'desktop') {
    return fail(400, 'not_the_app', 'Reports are sent from the Presentia desktop app.')
  }
  const { body, error } = await readJson(request, REPORT_MAX * 4 + 10_000)
  if (error) return error
  if (!(await allow(env, request, 'report'))) {
    return fail(429, 'rate_limited', 'Too many reports from this network. Try again in an hour.')
  }
  const text = String(body.text || '').slice(-REPORT_MAX)
  if (!text.trim()) return fail(400, 'empty', 'The report is empty.')
  const summary = oneLine(body.summary, 200)
  const version = oneLine(body.app_version, 40)
  const install = /^[0-9a-f]{16}$/.test(String(body.install_id || '')) ? body.install_id : (host ? host.id : null)
  await ensureReportsTable(env)
  let id = ''
  for (let i = 0; i < 3 && !id; i++) {
    const candidate = 'R-' + randomHex(4).toUpperCase()
    const res = await env.DB.prepare(
      `INSERT OR IGNORE INTO reports (id, host_id, created_at, app_version, summary, body)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6)`
    ).bind(candidate, install, nowIso(), version, summary, text).run()
    if (res.meta && res.meta.changes) id = candidate
  }
  if (!id) return fail(500, 'server_error', 'Could not store the report. Try again.')
  const github = !!(env.GITHUB_TOKEN && env.REPORTS_REPO) &&
    (await allowGlobal(env, 'github-issues', ISSUES_PER_DAY, 86_400))
  if (github) ctx.waitUntil(openIssue(env, { id, summary, version, text }).catch((e) => console.error(e)))
  return json({ id, github }, 201)
}

async function openIssue(env, report) {
  const repo = String(env.REPORTS_REPO).trim()
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) return console.error('REPORTS_REPO must look like owner/repo')
  const headers = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'presentia-reports',
  }
  const info = await fetch(`https://api.github.com/repos/${repo}`, { headers })
  if (!info.ok) return console.error(`GitHub: cannot read ${repo} (${info.status})`)
  if (!(await info.json()).private) {
    return console.error(`GitHub: ${repo} is public; diagnostic reports are only filed in private repos`)
  }
  const fence = '`'.repeat(4)
  const issue = await fetch(`https://api.github.com/repos/${repo}/issues`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: `[${report.id}] ${report.summary || 'Diagnostic report'}`.slice(0, 200),
      body: [
        `Diagnostic report **${report.id}** from Presentia ${report.version || '(unknown version)'}.`,
        '',
        report.summary,
        '',
        fence + 'text',
        report.text.slice(-60_000),
        fence,
      ].join('\n'),
    }),
  })
  if (!issue.ok) return console.error(`GitHub: issue not created (${issue.status}) ${await issue.text()}`)
  const { html_url } = await issue.json()
  await env.DB.prepare('UPDATE reports SET issue_url = ?1 WHERE id = ?2').bind(html_url, report.id).run()
}

// ── router ───────────────────────────────────────────────────────────────

async function handleApi(request, env, url, ctx) {
  const { pathname } = url
  const method = request.method
  let m

  if (pathname === '/api/health' && method === 'GET') return json({ ok: true, version: VERSION })

  if ((m = pathname.match(/^\/api\/classes\/([^/]+)$/)) && method === 'GET') {
    return getClass(env, request, decodeURIComponent(m[1]))
  }
  if (pathname === '/api/registrations' && method === 'POST') return submitRegistration(env, request)

  if (pathname === '/api/reports' && method === 'POST') return submitReport(env, request, ctx)
  if (pathname === '/api/host/register' && method === 'POST') return registerHost(env, request)

  if (pathname.startsWith('/api/host/')) {
    const host = await authHost(env, request)
    if (!host) return fail(401, 'unauthorized', 'Unknown or invalid host credentials.')
    if ((m = pathname.match(/^\/api\/host\/classes\/([^/]+)$/))) {
      if (method === 'PUT') return publishClass(env, request, host, m[1])
      if (method === 'DELETE') return unpublishClass(env, host, m[1])
    }
    if ((m = pathname.match(/^\/api\/host\/classes\/([^/]+)\/registrations$/)) && method === 'GET') {
      return listRegistrations(env, host, m[1], url)
    }
    if ((m = pathname.match(/^\/api\/host\/registrations\/([^/]+)$/)) && method === 'DELETE') {
      return deleteRegistration(env, host, m[1])
    }
    // Reports from desktop app 1.5.2 (newer versions use POST /api/reports).
    if (pathname === '/api/host/reports' && method === 'POST') return submitReport(env, request, ctx, host)
  }
  return fail(404, 'not_found', 'Unknown endpoint.')
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url)
    if (url.pathname.startsWith('/api/')) {
      try {
        return await handleApi(request, env, url, ctx)
      } catch (err) {
        console.error(err)
        return fail(500, 'server_error', 'Something went wrong. Please try again.')
      }
    }
    return env.ASSETS.fetch(request)
  },

  /** Daily: delete registrations nobody collected, stale rate-limit rows and old reports. */
  async scheduled(_event, env) {
    const cutoff = new Date(Date.now() - RETENTION_DAYS * 86_400_000).toISOString()
    await env.DB.batch([
      env.DB.prepare(
        'DELETE FROM photos WHERE registration_id IN (SELECT id FROM registrations WHERE created_at < ?1)'
      ).bind(cutoff),
      env.DB.prepare('DELETE FROM registrations WHERE created_at < ?1').bind(cutoff),
      env.DB.prepare('DELETE FROM rate_limits WHERE window_start < ?1')
        .bind(Math.floor(Date.now() / 1000) - 86_400),
    ])
    await ensureReportsTable(env)
    await env.DB.prepare('DELETE FROM reports WHERE created_at < ?1')
      .bind(new Date(Date.now() - REPORT_DAYS * 86_400_000).toISOString()).run()
  },
}
