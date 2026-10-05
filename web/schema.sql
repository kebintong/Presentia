-- Presentia online registration — Cloudflare D1 schema.
--
-- The website only *collects* registrations. Face data is never computed or
-- kept here: the desktop app downloads the photos, builds the face template
-- locally, and deletes the registration (photos included) from this database.
-- Anything not collected within RETENTION_DAYS is deleted by the daily cron.
--
-- Apply with:  npm run db:init        (remote, after `wrangler d1 create presentia`)
--              npm run db:init:local  (for `npm run dev`)

-- One row per Presentia desktop install that publishes classes. The secret
-- is only ever stored hashed (SHA-256).
CREATE TABLE IF NOT EXISTS hosts (
    id           TEXT PRIMARY KEY,
    secret_hash  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT
);

-- Classes a host has turned online registration on for.
CREATE TABLE IF NOT EXISTS classes (
    code        TEXT PRIMARY KEY,          -- 6-char join code, uppercase
    host_id     TEXT NOT NULL REFERENCES hosts(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    section     TEXT NOT NULL DEFAULT '',
    open        INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_classes_host ON classes(host_id);

-- Submitted, not yet collected by the desktop app.
CREATE TABLE IF NOT EXISTS registrations (
    id          TEXT PRIMARY KEY,
    class_code  TEXT NOT NULL REFERENCES classes(code) ON DELETE CASCADE,
    student_no  TEXT NOT NULL,
    name        TEXT NOT NULL,
    consent_at  TEXT NOT NULL,             -- when the student agreed to the privacy notice
    liveness    TEXT NOT NULL DEFAULT '{}',-- summary of the browser check (JSON)
    created_at  TEXT NOT NULL,
    UNIQUE (class_code, student_no)
);
CREATE INDEX IF NOT EXISTS idx_registrations_created ON registrations(created_at);

-- JPEG photos as base64 text (each well under D1's 2 MB row limit).
CREATE TABLE IF NOT EXISTS photos (
    registration_id TEXT NOT NULL REFERENCES registrations(id) ON DELETE CASCADE,
    idx             INTEGER NOT NULL,
    data            TEXT NOT NULL,
    PRIMARY KEY (registration_id, idx)
);

-- Fixed-window rate limits, keyed by purpose + hashed client IP.
CREATE TABLE IF NOT EXISTS rate_limits (
    key          TEXT PRIMARY KEY,
    count        INTEGER NOT NULL,
    window_start INTEGER NOT NULL
);

-- Diagnostic reports sent from the desktop app (Settings → Diagnostics).
-- Kept 30 days. The Worker also creates this table itself if it is missing.
CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    host_id     TEXT,
    created_at  TEXT NOT NULL,
    app_version TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL,
    issue_url   TEXT
);
