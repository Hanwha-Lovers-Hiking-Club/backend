-- 기본 RAG(지식카드 효과 검증용) 전용 SQL. main에 머지하지 않는다.
-- baseline_chunks 테이블(본문, 검색용 텍스트, 임베딩)과 match_baseline_chunks 벡터 검색 함수를 정의한다.
-- 팀 공용 tables.sql / functions.sql / docs/schema.md에는 넣지 않는다 (실험용이라서).
-- 여러 번 실행해도 안전하다.

create extension if not exists vector;

-- ---------------------------------------------------------------
-- baseline_chunks: 지식카드 없이 문서를 잘라 만든 청크와 그 임베딩
-- 문서 1개에 청크 N개. 문서를 다시 인덱싱하면 그 문서의 청크를 지우고 새로 넣는다.
-- ---------------------------------------------------------------
create table if not exists baseline_chunks (
  id          uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents (id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),  -- 문서 안 순서. 앞뒤 청크 조회에 쓴다
  location    text,  -- 섹션 경로. 예: "2장 · 답변 근거와 지식 연결"
  content     text not null check (length(trim(content)) > 0),  -- 청크 본문. 답변 컨텍스트와 근거 표시용
  search_text text not null check (length(trim(search_text)) > 0),  -- 헤더 + 문맥 설명 + 본문. 임베딩과 BM25의 대상
  embedding   vector(1024),  -- search_text의 kure-v3 1024차원 임베딩
  created_at  timestamptz not null default now(),
  -- 이 제약이 만드는 인덱스를 문서별 삭제와 앞뒤 청크 조회에도 쓴다
  unique (document_id, chunk_index)
);

-- 벡터 인덱스는 만들지 않는다. 청크 수가 적어서 전체 비교로 충분히 빠르고,
-- 근사 검색 때문에 정확도가 떨어지는 일도 없다.
-- BM25용 컬럼(tsvector 등)도 만들지 않는다. BM25는 파이썬에서 계산한다.

-- RLS: 켜기만 하고 정책은 만들지 않음 (백엔드 service role 전용)
alter table baseline_chunks enable row level security;

-- ---------------------------------------------------------------
-- match_baseline_chunks: 질문 벡터와 코사인 유사도가 높은 청크를 match_count개 돌려준다.
-- 유사도 하한선은 두지 않는다. 걸러내는 일은 RRF와 재순위 단계가 맡는다.
-- 파이썬에서는 supabase.rpc("match_baseline_chunks", {...})로 호출한다.
-- ---------------------------------------------------------------
create or replace function match_baseline_chunks(
  query_embedding vector(1024),
  match_count int default 20
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
    baseline_chunks.id,
    baseline_chunks.document_id,
    baseline_chunks.chunk_index,
    (1 - (baseline_chunks.embedding <=> query_embedding))::float as similarity
  from baseline_chunks
  where baseline_chunks.embedding is not null
  order by baseline_chunks.embedding <=> query_embedding
  limit match_count;
$$;
