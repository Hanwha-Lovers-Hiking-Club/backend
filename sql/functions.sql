-- Supabase SQL Editor에 붙여넣고 한 번 실행해두는 함수 모음.
-- PostgREST(SDK)는 pgvector 유사도 검색을 지원하지 않아서,
-- 이 파일에 Postgres 함수를 만들어두고 파이썬에서는 supabase.rpc("함수명", {...})로 호출한다.

create extension if not exists vector;


-- 챗봇 지식카드 검색용. active 카드만 대상으로, 지정한 임베딩 종류와 질문 벡터의 코사인 유사도 순으로 돌려준다.
-- embedding_kind: 'content'(전체) / 'situation'(상황·문제) / 'solution'(대응·해결) / 'lesson'(교훈).
-- 그 외 값은 'content'로 처리한다. 해당 임베딩이 NULL인 카드는 제외된다.
-- 파이썬에서는 supabase.rpc("match_knowledge_cards", {...})로 호출한다.
-- knowledge_cards의 임베딩 컬럼(vector(1024))은 sql/2026-10-28_add_knowledge_card_embeddings.sql 참고.
create or replace function match_knowledge_cards(
  query_embedding vector(1024),
  embedding_kind text default 'content',
  match_count int default 8,
  min_similarity float default 0.0
)
returns table (
  id uuid,
  similarity float
)
language sql stable
as $$
  select
    knowledge_cards.id,
    (1 - scored.distance)::float as similarity
  from knowledge_cards
  cross join lateral (
    select (
      case embedding_kind
        when 'situation' then knowledge_cards.situation_embedding
        when 'solution' then knowledge_cards.solution_embedding
        when 'lesson' then knowledge_cards.lesson_embedding
        else knowledge_cards.content_embedding
      end
    ) <=> query_embedding as distance
  ) as scored
  where knowledge_cards.status = 'active'
    and scored.distance is not null
    and 1 - scored.distance >= min_similarity
  order by scored.distance
  limit match_count;
$$;


-- 챗봇 원문 검색용. 질문 벡터와 코사인 유사도가 높은 원문 청크를 돌려준다.
-- 임베딩이 NULL인 청크는 제외된다. 걸러내는 일은 챗봇의 검증 단계가 맡으므로 하한은 낮게 둔다.
-- 파이썬에서는 supabase.rpc("match_document_chunks", {...})로 호출한다.
-- document_chunks 테이블(vector(1024))은 sql/2026-10-30_add_document_chunks.sql 참고.
create or replace function match_document_chunks(
  query_embedding vector(1024),
  match_count int default 20,
  min_similarity float default 0.0
)
returns table (
  id uuid,
  document_id uuid,
  chunk_index integer,
  similarity float
)
language sql stable
as $$
  select
    document_chunks.id,
    document_chunks.document_id,
    document_chunks.chunk_index,
    (1 - (document_chunks.embedding <=> query_embedding))::float as similarity
  from document_chunks
  where document_chunks.embedding is not null
    and 1 - (document_chunks.embedding <=> query_embedding) >= min_similarity
  order by document_chunks.embedding <=> query_embedding
  limit match_count;


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
