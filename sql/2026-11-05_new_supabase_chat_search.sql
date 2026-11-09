-- 신규 Supabase 프로젝트에 챗봇 검색(dev)용 테이블·RPC를 추가하는 스크립트.
-- 2026-11-05_new_supabase_pipeline.sql을 먼저 실행한 프로젝트에 적용한다.
-- 그 초기화 SQL에는 문서 업로드·카드 저장용 6개 테이블과 RPC 4개만 있고, 챗봇이 호출하는
-- document_chunks / match_knowledge_cards / match_document_chunks가 없다.
-- 정의는 sql/tables.sql, sql/functions.sql과 같다(여기서 컬럼·시그니처를 바꾸지 않는다).
-- 여러 번 실행해도 안전하다(if not exists / create or replace).
-- 이 파일은 작성만 했고 실제 DB에 실행해 검증하지 않았다. 실행은 팀장이 SQL Editor에서 한다.
-- 전체를 한 번에 붙여넣고 Run 한다. 중간에 실패하면 전부 되돌아간다.

begin;
-- Supabase는 vector 확장을 extensions 스키마에 둘 수 있어서 초기화 SQL과 같이 경로를 지정한다.
set local search_path = public, extensions;

create extension if not exists vector;

-- ---------------------------------------------------------------
-- document_chunks: 문서를 잘라 만든 청크 + 임베딩 (챗봇의 원문 검색용)
-- 문서 1개에 청크 N개. 카드와 직접 연결되지 않는다 (카드의 근거는 card_documents).
-- 벡터 인덱스는 아직 안 걺 — 데이터가 쌓이고 검색 패턴이 보이면 추가한다.
-- ---------------------------------------------------------------
create table if not exists document_chunks (
  id          uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents (id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),  -- 문서 안에서의 순서 (0부터)
  location    text,  -- 원문 내 위치. 예: "2장 · 답변 근거와 지식 연결"
  content     text not null check (length(trim(content)) > 0),  -- 청크 원문
  embedding   vector(1024),  -- kure-v3 1024차원. knowledge_cards 임베딩과 같은 모델/차원
  created_at  timestamptz not null default now(),
  unique (document_id, chunk_index)
);

alter table document_chunks enable row level security;
-- 초기화 SQL과 같이 새 프로젝트의 기본 grants에 의존하지 않는다
grant select, insert, delete on document_chunks to service_role;

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

grant execute on function match_knowledge_cards(vector, text, int, float) to service_role;
grant execute on function match_document_chunks(vector, int, float) to service_role;

-- API(service_role) 호출 세션의 search_path와 무관하게 vector 타입과 <=> 연산자를 찾게 한다.
alter function public.match_knowledge_cards(vector, text, int, float)
  set search_path = public, extensions;
alter function public.match_document_chunks(vector, int, float)
  set search_path = public, extensions;

-- PostgREST가 새 테이블·함수를 바로 인식하게 한다.
notify pgrst, 'reload schema';
commit;

-- 확인 1: document_chunks가 있고 rls_enabled가 true여야 한다.
select c.relname as table_name, c.relrowsecurity as rls_enabled
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname = 'document_chunks';

-- 확인 2: 검색 RPC 2개가 나와야 한다.
select p.proname as function_name, pg_get_function_identity_arguments(p.oid) as arguments
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname in ('match_knowledge_cards', 'match_document_chunks')
order by p.proname;
