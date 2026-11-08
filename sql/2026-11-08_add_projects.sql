-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- 업로드 1회를 프로젝트 1개로 묶기 위해 projects를 추가하고 문서와 카드를 연결한다.
-- 여러 번 실행해도 안전하다 (if not exists).
-- project_id는 지금 NULL을 허용한다. 기존 행을 채우고 검증한 뒤에 NOT NULL로 바꾼다.
-- 기존 행과 기존 컬럼(knowledge_cards.project_name 포함)은 바꾸지 않는다.

create table if not exists projects (
  id         uuid primary key default gen_random_uuid(),
  -- 같은 이름의 프로젝트도 별개로 유지하므로 유니크를 걸지 않는다
  name       text not null check (length(trim(name)) > 0),
  created_at timestamptz not null default now()
);

alter table projects enable row level security;

alter table documents add column if not exists project_id uuid references projects (id);
alter table knowledge_cards add column if not exists project_id uuid references projects (id);

create index if not exists documents_project_id_idx on documents (project_id);
create index if not exists knowledge_cards_project_id_idx on knowledge_cards (project_id);
