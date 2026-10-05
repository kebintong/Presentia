# Presentia online registration (website)

Students register themselves for a class from their own phone or laptop:

1. They open the website and enter the class **join code** (or open the link the instructor shares).
2. They type their student ID and full name, and agree to the privacy notice.
3. A short **live face check** runs in the browser (look at the camera, blink, turn the head, in a random
   order). Three photos are taken while they face the camera.
4. The registration waits on the website until the instructor's Presentia app collects it.

The desktop app (Students page → **Online registration**) downloads waiting registrations, builds each face
template **on the instructor's computer**, deletes the registration from the website, and lists it for the
instructor to **Accept** or **Reject**. Nothing is added to a class without the instructor accepting it.

It runs on Cloudflare's free plan: a Worker serves the site and a small API, and a D1 database holds
registrations until they are collected. Workers and D1 do not need a payment card. (Cloudflare's R2 file
storage does, which is why the photos are kept in D1 instead; they are small and short-lived.)

## One-time setup (about 10 minutes)

You need [Node.js](https://nodejs.org) 18 or newer and a free Cloudflare account
([sign up](https://dash.cloudflare.com/sign-up)).

```
cd web
npm install
npx wrangler login
npx wrangler d1 create presentia
```

The last command prints a `database_id`. Paste it into `wrangler.toml` in place of
`00000000-0000-0000-0000-000000000000`. Then create the tables and publish the site:

```
npm run db:init
npm run deploy
```

The first deploy asks you to choose a `workers.dev` subdomain. When it finishes it prints the address, for
example `https://presentia.your-name.workers.dev`. Open it to check that the join-code page appears.

## Connect the desktop app

Either:

- **Per computer:** in Presentia, open a class → **Students** → **Online registration**, paste the website
  address and press **Connect**. Then press **Turn on** for each class that should accept registrations.
- **For every install (recommended before a release):** set `DEFAULT_URL` in `app/data/cloud.py` to your
  address, then build and publish the release as usual. Installs that update pick it up automatically.

The app identifies itself to the website with credentials it creates on first use (stored in its own
database). Another teacher's install cannot see or collect your classes' registrations.

## Updating the website (automatic deploys)

The website is deployed by GitHub Actions (`.github/workflows/ci.yml`, job "Deploy the registration website"):
after a push to `main` that changes `web/`, **once every check has passed**, it runs `wrangler deploy`. A red
check means the website stays as it is. To deploy by hand, open Actions → CI → Run workflow on `main` and tick
"Also deploy the registration website". The website updates independently of the desktop app's releases; new
database columns and tables are added by the Worker itself.

One-time setup:

1. Cloudflare dashboard → **My Profile → API Tokens → Create Token** → template **Edit Cloudflare Workers** →
   Account Resources: your account; Zone Resources: All zones → add one more permission row,
   **Account · D1 · Edit** → Continue → Create Token. Copy the token (it is shown only once).
2. Cloudflare dashboard → **Workers & Pages** → copy the **Account ID** (right-hand side).
3. GitHub → the repository → **Settings → Secrets and variables → Actions → New repository secret**, twice:
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
4. Cloudflare → Workers & Pages → **presentia → Settings → Build** → disconnect the Git repository, so only
   GitHub Actions deploys (otherwise every push to `main` deploys even when checks fail).

Until the secrets exist, the deploy job only prints a note. `npm run deploy` from your own computer still works
as a manual fallback.

## Testing locally

```
npm run db:init:local
npm run dev            # http://127.0.0.1:8787
```

In the desktop app, use `http://127.0.0.1:8787` as the website address. On `localhost` only, adding `?test=1`
to the page address replaces the camera's face tracking with a simulated face, so the whole flow can be
clicked through without a real face (the desktop still checks the photos itself).

## How data is handled

- The browser face check runs on the student's device; only three 480 px photos are uploaded.
- Photos are deleted from the website as soon as the desktop app collects them. A daily job deletes anything
  not collected within **14 days**.
- Turning online registration off for a class (or deleting the class in the app) deletes everything still
  waiting for it on the website.
- The face-tracking library and model are served by the site itself (`public/vendor/`, copied from
  `node_modules` and `../models` at deploy time), so no third-party service sees the students.
- Per-IP rate limits are generous because a whole class on campus Wi-Fi usually shares one address.
- The privacy notice (`public/privacy.html`) refers to the Data Privacy Act of 2012. Have your school's data
  protection officer review it before using this with students.

## Diagnostic reports from the desktop app

When diagnostic mode is on (desktop app: Settings → Diagnostics) and something fails, the instructor can press
**Send report**. They see the whole report first. It is stored in the `reports` table for **30 days** (the
Worker creates the table itself; nothing to set up), and the app shows a report ID such as `R-91708279`.

Read reports in the Cloudflare dashboard → **Storage & Databases → D1 → presentia → Console**:

```
SELECT id, created_at, app_version, summary FROM reports ORDER BY created_at DESC;
SELECT body FROM reports WHERE id = 'R-91708279';
```

The desktop app sends reports to a fixed address of their own (`REPORTS_URL` in `app/data/cloud.py`), so changing
the registration website address in the app does not affect them. The endpoint (`POST /api/reports`) needs no
login; it only accepts requests from the app, at most 20 per hour per network and 200,000 characters each, and
opens at most 50 GitHub issues a day in total. `host_id` holds the sending install's random ID.

### Optional: also open a GitHub issue for each report

Reports can contain student names and numbers, so issues are only created in a **private** repository. The
Worker checks this and skips public repositories (the main Presentia repository is public, so use a separate one).

1. On GitHub, create a new **private** repository, for example `presentia-reports`.
2. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token. Repository access:
   **Only select repositories** → `presentia-reports`. Permissions → Repository → **Issues: Read and write**.
   Copy the token.
3. Cloudflare dashboard → Workers & Pages → **presentia** → Settings → **Variables and Secrets** → Add, twice,
   both of type **Secret**:
   - `GITHUB_TOKEN` = the token
   - `REPORTS_REPO` = `your-github-name/presentia-reports`
4. Send a test report from the app. A new issue titled `[R-…] …` appears in the private repository.

If the token expires or is wrong, reports are still stored in D1; the Worker's logs (Workers → presentia →
Logs) say why no issue was created.

## Limits on the free plan

Cloudflare's free plan includes 100,000 Worker requests per day, and D1 allows 5 million rows read and
100,000 rows written per day with 5 GB of storage. A registration uses a handful of each, so even hundreds
of students a day stay far below that. Check Cloudflare's current pricing pages if you plan something much
bigger.

## Files

| File | What it is |
|---|---|
| `src/worker.js` | API + static site + daily clean-up |
| `schema.sql` | D1 tables |
| `public/index.html`, `app.js`, `liveness.js`, `style.css` | Registration website |
| `public/privacy.html` | Privacy notice |
| `public/_headers` | Security headers (CSP, camera permission) |
| `scripts/copy-assets.mjs` | Copies MediaPipe and the face model into `public/vendor/` |
| `wrangler.toml` | Cloudflare configuration |
