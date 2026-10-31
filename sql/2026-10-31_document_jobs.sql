-- 팀장이 SQL Editor에서 적용: 기존 프로젝트의 작업/SSE 테이블과 함수.
-- sql/tables.sql 및 sql/functions.sql의 동일 정의를 신규 DB 기준으로도 유지한다.
create table if not exists document_jobs (
  id uuid primary key,
  document_ids uuid[] not null check (array_length(document_ids, 1) > 0),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'completed', 'partial', 'failed')),
  result jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create table if not exists document_job_events (
  id bigint generated always as identity primary key,
  job_id uuid not null references document_jobs (id) on delete cascade,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists document_job_events_job_id_id_idx
  on document_job_events (job_id, id);
alter table document_jobs enable row level security;
alter table document_job_events enable row level security;
grant select on document_jobs, document_job_events to service_role;
grant usage, select on sequence document_job_events_id_seq to service_role;

create or replace function create_document_job(p_job_id uuid, p_document_ids uuid[])
returns void language plpgsql security definer set search_path = public as $$
begin
  if p_document_ids is null or array_length(p_document_ids, 1) is null
     or array_length(p_document_ids, 1) > 10
     or (select count(distinct id) from unnest(p_document_ids) as source_id(id))
        <> array_length(p_document_ids, 1)
     or (select count(*) from documents where id = any(p_document_ids))
        <> array_length(p_document_ids, 1) then
    raise exception 'Invalid document job sources';
  end if;
  insert into document_jobs (id, document_ids) values (p_job_id, p_document_ids);
  insert into document_job_events (job_id, event_type, payload)
  values (p_job_id, 'queued', jsonb_build_object('jobId', p_job_id,
    'documentIds', to_jsonb(p_document_ids)));
end;
$$;
revoke all on function create_document_job(uuid, uuid[]) from public, anon, authenticated;
grant execute on function create_document_job(uuid, uuid[]) to service_role;

create or replace function record_document_job_event(
  p_job_id uuid, p_event_type text, p_payload jsonb, p_status text default null
)
returns bigint language plpgsql security definer set search_path = public as $$
declare
  v_status text;
  v_event_id bigint;
begin
  select status into v_status from document_jobs where id = p_job_id for update;
  if not found or v_status in ('completed', 'partial', 'failed') then
    raise exception 'Document job does not exist or is finished';
  end if;
  if p_event_type not in ('started', 'stage', 'completed', 'partial', 'failed')
     or (p_status is not null and p_status not in ('running', 'completed', 'partial', 'failed'))
     or (p_event_type in ('completed', 'partial', 'failed')
         and p_status is distinct from p_event_type)
     or (p_event_type = 'started' and p_status is distinct from 'running')
     or (p_event_type = 'stage' and p_status is not null) then
    raise exception 'Invalid document job event';
  end if;
  update document_jobs set
    status = coalesce(p_status, status),
    result = case when p_status in ('completed', 'partial', 'failed') then p_payload else result end,
    updated_at = now()
  where id = p_job_id;
  insert into document_job_events (job_id, event_type, payload)
  values (p_job_id, p_event_type, coalesce(p_payload, '{}'::jsonb)) returning id into v_event_id;
  return v_event_id;
end;
$$;
revoke all on function record_document_job_event(uuid, text, jsonb, text)
  from public, anon, authenticated;
grant execute on function record_document_job_event(uuid, text, jsonb, text) to service_role;
