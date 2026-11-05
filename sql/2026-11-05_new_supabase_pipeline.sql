-- 신규 Supabase 프로젝트의 문서 파이프라인 초기화 SQL (2026-11-05).
-- sql/tables.sql + sql/functions.sql의 현재 정의를 합친 배포용 스냅샷이다.
-- 테이블/함수의 원본 정의는 위 두 파일에서 관리한다.
-- Supabase SQL Editor에서 전체를 한 번에 실행한다. 실제 실행은 팀장 담당.
-- 기존 데이터 이전/기존 테이블 마이그레이션 스크립트가 아니다.
-- 대상 테이블이 하나라도 있으면 중단한다. DROP/TRUNCATE는 실행하지 않는다.
-- Storage 버킷은 별도로 documents (Private)로 생성한다.

begin;
set local search_path = public, extensions;

do $bootstrap$
declare
  v_name text;
begin
  foreach v_name in array array[
    'documents', 'knowledge_cards', 'card_documents',
    'document_save_receipts', 'document_jobs', 'document_job_events'
  ] loop
    if to_regclass('public.' || v_name) is not null then
      raise exception 'Fresh database required: public.% already exists', v_name;
    end if;
  end loop;
  if exists (
    select 1 from pg_extension e join pg_namespace n on n.oid = e.extnamespace
    where e.extname = 'vector' and n.nspname not in ('public', 'extensions')
  ) then
    raise exception 'vector extension must be in public or extensions; inspect before installing';
  end if;
end;
$bootstrap$;

-- BEGIN SOURCE: tables.sql
-- SHA256 (UTF-8/LF): 53de479a214a1e8b67c8c5d77a3d440921ad435ebd00f8bb5bec188ac5832d0e
-- Supabase SQL Editor에 붙여넣고 실행하는 테이블 생성 스크립트.
-- 테이블을 바꾸면 docs/schema.md도 같은 PR에서 같이 업데이트할 것.
--
-- 접근 방식: RLS를 켜고 정책은 만들지 않는다.
-- anon/authenticated 키로는 접근 불가, 백엔드의 service role 키만 접근 가능(RLS 우회).
-- 회원 시스템이 생기면 user_id 컬럼과 정책을 추가한다.
create extension if not exists vector;

-- ---------------------------------------------------------------
-- documents: 원본 문서 메타. 실제 파일은 Supabase Storage에 있다.
-- ---------------------------------------------------------------
create table if not exists documents (
  id                uuid primary key default gen_random_uuid(),
  title             text not null check (length(trim(title)) > 0),  -- 현재 원래 파일명
  original_filename text not null check (length(trim(original_filename)) > 0),  -- 사용자가 올린 원래 파일명
  size_bytes        bigint not null check (size_bytes >= 0),  -- 파일 용량(바이트)
  storage_bucket    text not null check (length(trim(storage_bucket)) > 0),
  storage_path      text not null check (length(trim(storage_path)) > 0),
  created_at        timestamptz not null default now(),
  unique (storage_bucket, storage_path)
);

-- ---------------------------------------------------------------
-- knowledge_cards: 지식카드 본문 + 메타데이터
-- ---------------------------------------------------------------
create table if not exists knowledge_cards (
  id                   uuid primary key default gen_random_uuid(),

  -- 본문: 기본 필수 5개 중 source는 card_documents로 연결
  title                text not null check (length(trim(title)) > 0),
  summary              text not null check (length(trim(summary)) > 0),
  context              text not null check (length(trim(context)) > 0),
  lesson               text not null check (length(trim(lesson)) > 0),

  -- 조건부 필수: 아래 5개 중 최소 1개는 실질적인 값이 있어야 함
  problem              text,
  cause                text,
  approach             text,
  rationale            text,
  outcome              text,

  technologies         text[] not null default '{}',  -- 기술 스택 (WebSocket, Supabase 등)
  keywords             text[] not null default '{}',  -- 분야/주제어 (웹 개발, QA, 네트워크 등)

  department           text,  -- 담당 부서. 식별 불가하면 NULL
  project_date         date,  -- 지식이 나온 프로젝트 시점. 식별 불가하면 NULL

  -- 메타데이터 (v1.0)
  project_name         text,  -- LLM이 문서에서 추출한 프로젝트 이름. 식별 불가하면 NULL
  status               text not null default 'draft'
                         constraint knowledge_cards_status_check
                         check (status in ('draft', 'active', 'flagged', 'archived')),
  quality_score        numeric,
  last_verified_at     timestamptz,
  verification_version text,
  content_embedding    vector(1024),
  situation_embedding  vector(1024),
  solution_embedding   vector(1024),
  lesson_embedding     vector(1024),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),

  constraint knowledge_cards_conditional_fields_check check (
    coalesce(
      nullif(trim(problem), ''),
      nullif(trim(cause), ''),
      nullif(trim(approach), ''),
      nullif(trim(rationale), ''),
      nullif(trim(outcome), '')
    ) is not null
  )
);

create index if not exists knowledge_cards_status_idx on knowledge_cards (status);
create index if not exists knowledge_cards_technologies_idx on knowledge_cards using gin (technologies);
create index if not exists knowledge_cards_keywords_idx on knowledge_cards using gin (keywords);

-- updated_at 자동 갱신
create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists knowledge_cards_set_updated_at on knowledge_cards;
create trigger knowledge_cards_set_updated_at
  before update on knowledge_cards
  for each row execute function set_updated_at();

-- ---------------------------------------------------------------
-- card_documents: 지식카드 <-> 원본 문서 근거 (카드의 source)
-- 행 하나가 근거 하나다. 한 카드가 같은 문서의 여러 부분을 근거로 하면 행이 여러 개 생긴다.
-- "카드당 근거 1개 이상"은 DB로 강제하기 어려워 앱 로직에서 보장한다.
-- ---------------------------------------------------------------
create table if not exists card_documents (
  id          uuid primary key default gen_random_uuid(),
  card_id     uuid not null references knowledge_cards (id) on delete cascade,
  document_id uuid not null references documents (id) on delete cascade,
  location    text,  -- 원문 내 위치. 예: "2장 · 답변 근거와 지식 연결"
  excerpt     text   -- 원문에서 그대로 가져온 근거 발췌문
);

create index if not exists card_documents_card_id_idx on card_documents (card_id);
create index if not exists card_documents_document_id_idx on card_documents (document_id);

-- 문서별 저장 요청의 완료 확인과 재시도 중복 방지.
create table if not exists document_save_receipts (
  document_id uuid primary key references documents (id) on delete cascade,
  payload_hash text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
  card_ids uuid[] not null default '{}',
  created_at timestamptz not null default now()
);
grant select, insert, update on document_save_receipts to service_role;

-- 업로드 후 실행되는 작업과 재접속 가능한 SSE 이력.
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
grant select on document_jobs, document_job_events to service_role;
grant usage, select on sequence document_job_events_id_seq to service_role;

-- ---------------------------------------------------------------
-- RLS: 켜기만 하고 정책은 만들지 않음 (백엔드 service role 전용)
-- ---------------------------------------------------------------
alter table documents enable row level security;
alter table knowledge_cards enable row level security;
alter table card_documents enable row level security;
alter table document_save_receipts enable row level security;
alter table document_jobs enable row level security;
alter table document_job_events enable row level security;
-- END SOURCE: tables.sql

-- BEGIN SOURCE: functions.sql
-- SHA256 (UTF-8/LF): 103b6064304d118edbe09fa9d9071a462c81950a408cbf9825d824c548e877cb
-- Supabase SQL Editor에 붙여넣고 한 번 실행해두는 함수 모음.
-- 기존 match_documents 예시는 documents에 없는 content/embedding 컬럼을
-- 참조하므로 제거했다. 카드 검색 RPC는 검색 담당 범위에서 정의한다.

create extension if not exists vector;

-- 검증을 통과한 한 문서의 카드·근거·벡터·완료 영수증을 원자적으로 저장한다.
-- 동일한 document_id/payload_hash 재호출은 기존 카드 ID를 돌려준다.
-- 다른 payload_hash로 같은 문서를 다시 저장하려면 별도 재처리 정책이 필요하다.
-- p_cards=[]도 처리 완료로 기록하며 빈 card_ids 영수증을 반환한다.
-- pass 카드만 입력되고 active 공개는 이 트랜잭션이 커밋될 때 시작한다.
create or replace function save_document_cards(
  p_document_id uuid,
  p_payload_hash text,
  p_cards jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_inserted integer;
  v_receipt document_save_receipts%rowtype;
  v_card jsonb;
  v_evidence jsonb;
  v_card_id uuid;
  v_card_ids uuid[] := '{}'::uuid[];
begin
  if p_payload_hash !~ '^[0-9a-f]{64}$' or jsonb_typeof(p_cards) is distinct from 'array' then
    raise exception 'Invalid document save request';
  end if;
  perform 1 from documents where id = p_document_id for update;
  if not found then
    raise exception 'Source document does not exist';
  end if;

  insert into document_save_receipts (document_id, payload_hash)
  values (p_document_id, p_payload_hash)
  on conflict (document_id) do nothing;
  get diagnostics v_inserted = row_count;
  if v_inserted = 0 then
    select * into v_receipt from document_save_receipts
    where document_id = p_document_id for update;
    if v_receipt.payload_hash is distinct from p_payload_hash then
      raise exception 'Document was already saved with different content';
    end if;
    return jsonb_build_object(
      'document_id', p_document_id, 'payload_hash', p_payload_hash,
      'committed', true, 'result_recorded', true, 'card_ids', to_jsonb(v_receipt.card_ids)
    );
  end if;

  for v_card in select value from jsonb_array_elements(p_cards) loop
    if jsonb_typeof(v_card->'evidences') is distinct from 'array'
       or jsonb_array_length(v_card->'evidences') = 0 then
      raise exception 'Every saved card requires source evidence';
    end if;
    insert into knowledge_cards (
      title, summary, context, problem, cause, approach, rationale, outcome, lesson,
      technologies, keywords, status, content_embedding, situation_embedding,
      solution_embedding, lesson_embedding
    ) values (
      v_card->>'title', v_card->>'summary', v_card->>'context', v_card->>'problem',
      v_card->>'cause', v_card->>'approach', v_card->>'rationale', v_card->>'outcome',
      v_card->>'lesson',
      array(select jsonb_array_elements_text(v_card->'technologies')),
      array(select jsonb_array_elements_text(v_card->'keywords')),
      'active',
      (v_card->>'content_embedding')::vector(1024),
      (v_card->>'situation_embedding')::vector(1024),
      (v_card->>'solution_embedding')::vector(1024),
      (v_card->>'lesson_embedding')::vector(1024)
    ) returning id into v_card_id;
    v_card_ids := array_append(v_card_ids, v_card_id);
    for v_evidence in select value from jsonb_array_elements(v_card->'evidences') loop
      insert into card_documents (card_id, document_id, location, excerpt)
      values (v_card_id, p_document_id, v_evidence->>'location', v_evidence->>'excerpt');
    end loop;
  end loop;

  update document_save_receipts set card_ids = v_card_ids where document_id = p_document_id;
  return jsonb_build_object(
    'document_id', p_document_id, 'payload_hash', p_payload_hash,
    'committed', true, 'result_recorded', true, 'card_ids', to_jsonb(v_card_ids)
  );
end;
$$;

revoke all on function save_document_cards(uuid, text, jsonb) from public, anon, authenticated;
grant execute on function save_document_cards(uuid, text, jsonb) to service_role;

-- 작업 등록과 첫 이벤트를 한 트랜잭션에서 기록한다.
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

-- 상태 갱신과 이벤트 기록을 원자적으로 묶는다. 종료한 작업은 다시 쓰지 않는다.
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

-- 웹 프로세스 중단 등으로 30분간 갱신되지 않은 작업을 종료한다.
-- 행 잠금 뒤 갱신 시각을 다시 확인해 살아 있는 작업과 경합하지 않는다.
create or replace function expire_stale_document_job(p_job_id uuid)
returns boolean language plpgsql security definer set search_path = public as $$
declare
  v_job document_jobs%rowtype;
  v_result jsonb := jsonb_build_object('errorCode', 'JOB_INTERRUPTED');
begin
  select * into v_job from document_jobs where id = p_job_id for update;
  if not found or v_job.status in ('completed', 'partial', 'failed')
     or v_job.updated_at > now() - interval '30 minutes' then
    return false;
  end if;
  update document_jobs
    set status = 'failed', result = v_result, updated_at = now()
    where id = p_job_id;
  insert into document_job_events (job_id, event_type, payload)
    values (p_job_id, 'failed', v_result);
  return true;
end;
$$;
revoke all on function expire_stale_document_job(uuid) from public, anon, authenticated;
grant execute on function expire_stale_document_job(uuid) to service_role;
-- END SOURCE: functions.sql

-- 신규 프로젝트의 기본 권한 설정과 무관하게 백엔드 권한을 명시한다.
grant usage on schema public to service_role;
grant select, insert, update, delete on
  public.documents, public.knowledge_cards, public.card_documents
  to service_role;
grant select, insert, update on public.document_save_receipts to service_role;
grant select on public.document_jobs, public.document_job_events to service_role;
grant usage, select on sequence public.document_job_events_id_seq to service_role;

-- 호출 세션의 search_path와 무관하게 저장 RPC가 vector 타입을 찾게 한다.
-- SECURITY INVOKER는 원본 함수 정의 그대로 유지한다.
alter function public.save_document_cards(uuid, text, jsonb)
  set search_path = public, extensions;

notify pgrst, 'reload schema';
commit;

-- 생성 확인: 6개 행이 나오고 rls_enabled가 모두 true여야 한다.
select c.relname as table_name, c.relrowsecurity as rls_enabled
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in (
  'documents', 'knowledge_cards', 'card_documents',
  'document_save_receipts', 'document_jobs', 'document_job_events'
)
order by c.relname;

-- 저장/Job RPC 4개가 나와야 한다.
select p.proname as function_name, pg_get_function_identity_arguments(p.oid) as arguments
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname in (
  'save_document_cards', 'create_document_job',
  'record_document_job_event', 'expire_stale_document_job'
)
order by p.proname;
