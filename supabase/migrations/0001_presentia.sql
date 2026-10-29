-- Presentia cloud sync (Supabase). Shared by Presentia Desktop and the phone app.
--
-- Every row belongs to one teacher (owner_id = their Supabase account) and
-- row-level security lets each teacher see only their own rows. Apply with
-- the Supabase CLI (`supabase db push`) or paste into the SQL editor.
--
-- Sync rules, the same for every table:
--   id          a UUID made by whichever device created the row
--   updated_at  when the row was last changed, by the device's clock; an
--               older change arriving late never overwrites a newer one
--   server_at   when the server stored it; devices ask for "everything
--               changed since" through presentia_changes()
--   deleted     rows are never removed, only marked, so every device learns
--               about the delete; marking one wipes its names and face data
--
-- Times the teacher sees (created_at, started_at, time_in, ...) are stored as
-- the desktop writes them, local time as text, so no time zone is ever
-- applied twice.

create table public.classes (
    id             uuid primary key,
    owner_id       uuid not null default auth.uid() references auth.users on delete cascade,
    name           text not null,
    section        text not null default '',
    join_code      text not null default '',
    online         boolean not null default false,
    created_at     text not null,
    last_opened_at text,
    updated_at     timestamptz not null default now(),
    server_at      timestamptz not null default now(),
    deleted        boolean not null default false
);

create table public.students (
    id          uuid primary key,
    owner_id    uuid not null default auth.uid() references auth.users on delete cascade,
    student_no  text not null,
    name        text not null,
    embedding   text,                  -- base64 of 512 little-endian float32 (ArcFace w600k_r50)
    created_at  text not null,
    updated_at  timestamptz not null default now(),
    server_at   timestamptz not null default now(),
    deleted     boolean not null default false
);

-- Extra pictures of a face: registration angles, "This is…", learned in meetings.
create table public.student_faces (
    id          uuid primary key,
    owner_id    uuid not null default auth.uid() references auth.users on delete cascade,
    student_id  uuid not null references public.students,
    embedding   text,
    source      text not null,
    created_at  text not null,
    updated_at  timestamptz not null default now(),
    server_at   timestamptz not null default now(),
    deleted     boolean not null default false
);

create table public.class_students (
    id          uuid primary key,
    owner_id    uuid not null default auth.uid() references auth.users on delete cascade,
    class_id    uuid not null references public.classes,
    student_id  uuid not null references public.students,
    added_at    text not null,
    updated_at  timestamptz not null default now(),
    server_at   timestamptz not null default now(),
    deleted     boolean not null default false
);

-- A meeting monitored on the desktop, or an in-person class on the phone.
create table public.sessions (
    id          uuid primary key,
    owner_id    uuid not null default auth.uid() references auth.users on delete cascade,
    class_id    uuid references public.classes,
    name        text not null,
    started_at  text not null,
    ended_at    text,
    updated_at  timestamptz not null default now(),
    server_at   timestamptz not null default now(),
    deleted     boolean not null default false
);

create table public.attendance (
    id          uuid primary key,
    owner_id    uuid not null default auth.uid() references auth.users on delete cascade,
    session_id  uuid not null references public.sessions,
    student_id  uuid not null references public.students,
    time_in     text not null,
    time_out    text,
    status      text not null default 'Present',
    updated_at  timestamptz not null default now(),
    server_at   timestamptz not null default now(),
    deleted     boolean not null default false
);

-- Registrations from the website, after the desktop downloaded them and built
-- the face template (the template itself stays on the desktop). Either device
-- may decide: the first decision wins, because a decision is only written
-- where `decision is null` (PATCH ...?decision=is.null&deleted=is.false,
-- without updated_at, so no device's clock can get in the way). The desktop
-- then adds the student and marks the row deleted.
create table public.pending_students (
    id           uuid primary key,
    owner_id     uuid not null default auth.uid() references auth.users on delete cascade,
    class_id     uuid not null references public.classes,
    student_no   text not null,
    name         text not null,
    photo_jpeg   text,                 -- base64 thumbnail for the teacher to compare
    problem      text not null default '',
    submitted_at text not null,
    action       text not null default 'new',  -- what accepting does: new | join | in_class | conflict | number_taken | no_face
    action_note  text not null default '',     -- e.g. "Number 2024-001 belongs to Maria Santos"
    decision     text check (decision in ('accepted', 'declined')),
    decided_by   text check (decided_by in ('desktop', 'phone')),
    decided_at   timestamptz,
    updated_at   timestamptz not null default now(),
    server_at    timestamptz not null default now(),
    deleted      boolean not null default false
);

-- Phone app: a student picked at random to answer a question, marked by the teacher.
create table public.recitations (
    id          uuid primary key,
    owner_id    uuid not null default auth.uid() references auth.users on delete cascade,
    session_id  uuid not null references public.sessions,
    student_id  uuid not null references public.students,
    question    text not null default '',
    correct     boolean,               -- null until the teacher marks it
    asked_at    text not null,
    updated_at  timestamptz not null default now(),
    server_at   timestamptz not null default now(),
    deleted     boolean not null default false
);

-- Last change wins; deleting wipes personal and face data.
create function public.presentia_touch() returns trigger
language plpgsql set search_path = '' as $$
begin
    -- Deleted stays deleted; otherwise an older change arriving late is ignored.
    if tg_op = 'UPDATE' and (old.deleted or (new.updated_at < old.updated_at and not new.deleted)) then
        return old;
    end if;
    if new.deleted then
        -- Keys a table does not have are ignored.
        new := jsonb_populate_record(new, '{"name": "", "student_no": "", "embedding": null,
            "photo_jpeg": null, "question": "", "section": "", "join_code": ""}');
    end if;
    new.server_at := clock_timestamp();
    return new;
end $$;

do $$
declare t text;
begin
    foreach t in array array['classes', 'students', 'student_faces', 'class_students',
                             'sessions', 'attendance', 'pending_students', 'recitations'] loop
        execute format('create trigger presentia_touch before insert or update on public.%I
                        for each row execute function public.presentia_touch()', t);
        execute format('create index on public.%I (owner_id, server_at)', t);
        execute format('alter table public.%I enable row level security', t);
        execute format('create policy "own rows" on public.%I for all to authenticated
                        using (owner_id = (select auth.uid()))
                        with check (owner_id = (select auth.uid()))', t);
    end loop;
end $$;

-- Everything of the caller's that changed after `since`, one list per table.
-- Row-level security applies (security invoker), so it is only ever theirs.
create function public.presentia_changes(since timestamptz)
returns jsonb language sql stable security invoker set search_path = '' as $$
    select jsonb_build_object(
        'classes',          coalesce((select jsonb_agg(t) from public.classes t where server_at > since), '[]'),
        'students',         coalesce((select jsonb_agg(t) from public.students t where server_at > since), '[]'),
        'student_faces',    coalesce((select jsonb_agg(t) from public.student_faces t where server_at > since), '[]'),
        'class_students',   coalesce((select jsonb_agg(t) from public.class_students t where server_at > since), '[]'),
        'sessions',         coalesce((select jsonb_agg(t) from public.sessions t where server_at > since), '[]'),
        'attendance',       coalesce((select jsonb_agg(t) from public.attendance t where server_at > since), '[]'),
        'pending_students', coalesce((select jsonb_agg(t) from public.pending_students t where server_at > since), '[]'),
        'recitations',      coalesce((select jsonb_agg(t) from public.recitations t where server_at > since), '[]')
    )
$$;

-- Live updates for the phone (Supabase Realtime), so a registration accepted
-- on one device disappears from the other right away.
do $$
begin
    if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
        alter publication supabase_realtime add table public.pending_students, public.attendance;
    end if;
end $$;
