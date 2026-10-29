# Presentia cloud (Supabase)

Shared by Presentia Desktop (Settings → Phone app) and the Presentia phone app. Optional: a teacher who
never signs in keeps everything on their computer.

## One-time setup

1. **Region.** Create the project in *Southeast Asia (Singapore)*, closest to the Philippines.
2. **Tables.** Run `migrations/0001_presentia.sql` in the SQL editor (or `supabase db push`).
3. **Sign-in by code.** Authentication → Sign In / Providers → Email: keep *Email* on.
   Authentication → Emails: in **both** the *Confirm signup* template (a teacher's first sign-in) and
   the *Magic Link* template (every sign-in after), replace `{{ .ConfirmationURL }}` with the code, e.g.
   `<p>Your Presentia code: <strong>{{ .Token }}</strong></p>`. The apps ask for that code; they
   can't open a link. One code per email address every 60 seconds; the desktop counts that down.
4. **Email sending.** Supabase's built-in email sends only a few emails an hour and only to your own
   team's addresses. Before teachers use it, set up your own SMTP under Authentication → Emails → SMTP
   (Resend, Brevo, your school's mail server).
5. **Keys in the apps.** Project Settings → API: copy the *Project URL* and the *publishable / anon*
   key into `SUPABASE_URL` and `SUPABASE_KEY` in `app/data/sync.py` (and the phone app). They are
   safe to ship; row-level security decides who sees what. **Never** put the `service_role` key in an
   app or in this repository.

For testing without editing the code: `PRESENTIA_SUPABASE_URL` and `PRESENTIA_SUPABASE_KEY`.

## How the data moves

| Table | Written by | Notes |
|---|---|---|
| classes, students, student_faces, class_students | desktop | face templates as base64 float32 (ArcFace `w600k_r50`, 512 numbers) |
| sessions, attendance | desktop and phone | the phone adds in-person sessions |
| pending_students | desktop adds; either decides | website registrations after the desktop made the face template |
| recitations | phone | random recitation, marked correct or wrong |

- Every row has a UUID made by the device that created it, `updated_at` (that device's clock) and
  `server_at` (set by the server). The newest `updated_at` wins; deleted rows stay deleted, with their
  names and face data wiped.
- Devices fetch changes with `rpc/presentia_changes(since)` and upload with an upsert
  (`POST /rest/v1/<table>?on_conflict=id`, `Prefer: resolution=merge-duplicates`).
- **Deciding a registration** (phone app): `PATCH /rest/v1/pending_students?id=eq.<id>&decision=is.null&deleted=is.false`
  with `{"decision": "accepted" | "declined", "decided_by": "phone", "decided_at": <now>}` and
  `Prefer: return=representation`. An empty answer means the desktop decided first. Don't send
  `updated_at` with a decision. The desktop adds the student within a few seconds and then marks the row
  deleted. If it can't (for example the student number belongs to someone else), it clears the decision
  and explains why in `action_note`.
- `pending_students` and `attendance` are published to Realtime, so the phone can drop a prompt the
  moment the desktop decides it.
