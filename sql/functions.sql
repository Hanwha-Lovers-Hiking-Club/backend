-- Supabase SQL Editor에 붙여넣고 한 번 실행해두는 함수 모음.
-- PostgREST(SDK)는 pgvector 유사도 검색을 지원하지 않아서,
-- 이 함수를 만들어두고 파이썬에서는 supabase.rpc("match_documents", {...})로 호출한다.
--
-- 전제: documents 테이블에 content(text), embedding(vector(1536)) 컬럼이 있다고 가정.
-- 실제 컬럼/차원 수는 쓰는 임베딩 모델에 맞게 수정할 것.

create extension if not exists vector;

create or replace function match_documents(
  query_embedding vector(1536),
  match_count int default 5
)
returns table (
  id bigint,
  content text,
  similarity float
)
language sql stable
as $$
  select
    documents.id,
    documents.content,
    1 - (documents.embedding <=> query_embedding) as similarity
  from documents
  order by documents.embedding <=> query_embedding
  limit match_count;
$$;


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
