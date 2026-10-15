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
