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
  title             text not null check (length(trim(title)) > 0),  -- LLM이 문서에서 추출
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
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),

  -- 검색용 임베딩 4종 (임베딩 설계 가이드 참고). 차원 1024는 kure-v3 정확도 손실이 적은 선에서
  -- pgvector 인덱스 상한선(2,000차원)보다 여유 있게 잡은 값. 인덱스는 아직 안 걺(필요해지면 추가)
  content_embedding    vector(1024),
  situation_embedding  vector(1024),
  solution_embedding   vector(1024),
  lesson_embedding     vector(1024),

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

-- ---------------------------------------------------------------
-- RLS: 켜기만 하고 정책은 만들지 않음 (백엔드 service role 전용)
-- ---------------------------------------------------------------
alter table documents enable row level security;
alter table knowledge_cards enable row level security;
alter table card_documents enable row level security;
