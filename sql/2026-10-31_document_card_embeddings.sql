-- 기존 Supabase DB에 적용한다. 실제 SQL Editor 적용은 팀장 담당이다.
-- 운영 DB의 네 컬럼은 2026-10-31 확인 시 이미 vector(1024)였다.
-- 아래 ADD는 아직 없는 환경에서도 동일한 구조를 만들기 위한 것이다.
create extension if not exists vector;

alter table knowledge_cards add column if not exists content_embedding vector(1024);
alter table knowledge_cards add column if not exists situation_embedding vector(1024);
alter table knowledge_cards add column if not exists solution_embedding vector(1024);
alter table knowledge_cards add column if not exists lesson_embedding vector(1024);

create table if not exists document_save_receipts (
  document_id uuid primary key references documents (id) on delete cascade,
  payload_hash text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
  card_ids uuid[] not null default '{}',
  created_at timestamptz not null default now()
);
grant select, insert, update on document_save_receipts to service_role;

alter table document_save_receipts enable row level security;

-- 다음에 sql/functions.sql의 save_document_cards 함수를 적용한다.
