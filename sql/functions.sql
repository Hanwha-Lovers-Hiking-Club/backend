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
$$;
