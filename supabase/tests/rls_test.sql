-- Checks the sync rules and row-level security of 0001_presentia.sql.
-- Run after auth_stub.sql and the migration; any failed check stops with an error.
\set ON_ERROR_STOP 1

grant usage on schema public to authenticated, anon;
grant all on all tables in schema public to authenticated, anon;
grant execute on all functions in schema public to authenticated, anon;

create function public.expect(ok boolean, what text) returns void language plpgsql as $$
begin
    if ok is not true then raise exception 'FAILED: %', what; end if;
end $$;
grant execute on function public.expect(boolean, text) to authenticated, anon;

insert into auth.users values ('11111111-1111-1111-1111-111111111111'), ('22222222-2222-2222-2222-222222222222');

set role authenticated;
select set_config('request.jwt.claim.sub', '11111111-1111-1111-1111-111111111111', false);
insert into classes (id, name, created_at, updated_at)
    values ('aaaaaaaa-0000-4000-8000-000000000001', 'IT 301', '2026-10-29 08:00:00', '2026-10-29T00:00:00Z');
insert into students (id, student_no, name, embedding, created_at, updated_at)
    values ('bbbbbbbb-0000-4000-8000-000000000001', '2024-001', 'Juan', 'AAAA', 'x', '2026-10-29T00:00:00Z');
insert into pending_students (id, class_id, student_no, name, submitted_at)
    values ('cccccccc-0000-4000-8000-000000000001', 'aaaaaaaa-0000-4000-8000-000000000001', '2024-002', 'Maria', 'x');

-- The newest change wins.
update classes set name = 'old', updated_at = '2026-10-28T00:00:00Z';
select expect((select name from classes) = 'IT 301', 'an older change must not overwrite a newer one');
update classes set name = 'IT 302', updated_at = '2026-10-29T01:00:00Z';
select expect((select name from classes) = 'IT 302', 'a newer change is applied');

-- The first decision on a registration wins.
with d as (update pending_students set decision = 'accepted', decided_by = 'phone'
           where decision is null and not deleted returning 1)
select expect((select count(*) from d) = 1, 'the first decision is recorded');
with d as (update pending_students set decision = 'declined', decided_by = 'desktop'
           where decision is null and not deleted returning 1)
select expect((select count(*) from d) = 0, 'a second decision is refused');
select expect((select decision from pending_students) = 'accepted', 'the first decision stays');

-- Deleting wipes names and face data, even from a device with an older clock, and stays deleted.
update students set deleted = true, updated_at = '2026-10-01T00:00:00Z';
update students set name = 'back', updated_at = '2027-01-01T00:00:00Z';
select expect((select embedding is null and name = '' and student_no = '' and deleted from students),
              'a deleted student keeps no name or face data');

-- presentia_changes() returns only what changed since.
select expect(jsonb_array_length(presentia_changes('-infinity')->'classes') = 1, 'changes() returns own rows');
select expect(jsonb_array_length(presentia_changes(now() + interval '1 hour')->'classes') = 0,
              'changes() filters by time');

-- Another teacher sees and changes nothing of ours.
select set_config('request.jwt.claim.sub', '22222222-2222-2222-2222-222222222222', false);
select expect((select count(*) from classes) = 0, 'other teachers cannot read our classes');
select expect((select count(*) from students) = 0, 'other teachers cannot read our students');
select expect(jsonb_array_length(presentia_changes('-infinity')->'students') = 0, 'changes() hides others');
with u as (update classes set name = 'hacked' returning 1)
select expect((select count(*) from u) = 0, 'other teachers cannot change our classes');
do $$ begin
    insert into classes (id, owner_id, name, created_at)
        values (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'x', 'x');
    raise exception 'FAILED: inserting a row owned by someone else was allowed';
exception when insufficient_privilege then null;
end $$;

-- Signed out: nothing.
reset role;
set role anon;
select set_config('request.jwt.claim.sub', '', false);
select expect((select count(*) from classes) = 0, 'signed-out callers read nothing');

\echo 'Supabase rules: all checks passed'
