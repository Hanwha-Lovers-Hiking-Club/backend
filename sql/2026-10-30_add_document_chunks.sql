-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- 문서를 잘라 만든 청크와 그 임베딩을 저장하는 document_chunks를 추가한다.
-- 챗봇이 지식카드에 없는 원문 내용을 검색할 때 쓴다 (카드가 안 만들어진 문서도 검색 가능).
-- 임베딩은 kure-v3 1024차원으로 knowledge_cards의 임베딩과 같다. 벡터 인덱스는 아직 안 만든다 —
-- 데이터가 쌓이고 검색 패턴이 보이면 추가한다.
-- 여러 번 실행해도 안전하다. 이 스크립트 실행 전에 업로드된 문서에는 청크가 없다(필요하면 backfill).

create table if not exists document_chunks (
  id          uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents (id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  location    text,
  content     text not null check (length(trim(content)) > 0),
  embedding   vector(1024),
  created_at  timestamptz not null default now(),
  unique (document_id, chunk_index)
);

alter table document_chunks enable row level security;
