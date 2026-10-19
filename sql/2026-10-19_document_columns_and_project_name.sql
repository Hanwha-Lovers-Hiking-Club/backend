-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- 여러 번 실행해도 안전하다 (if not exists / if exists).
-- 전제: documents, knowledge_cards 테이블이 비어 있어야 한다.
--   - documents.original_filename, size_bytes 는 not null 이라 기존 행이 있으면 실패한다.
--   - knowledge_cards.project_id 컬럼을 삭제하므로 그 안의 값은 사라진다.

-- documents: 사용자가 올린 원래 파일명 (title은 LLM이 뽑는 값이라 원본 이름이 남지 않음)
alter table documents
  add column if not exists original_filename text not null
  check (length(trim(original_filename)) > 0);

-- documents: 파일 용량(바이트)
alter table documents
  add column if not exists size_bytes bigint not null
  check (size_bytes >= 0);

-- knowledge_cards: project_id(uuid) 대신 LLM이 추출한 project_name(text) 사용
alter table knowledge_cards add column if not exists project_name text;

drop index if exists knowledge_cards_project_id_idx;
alter table knowledge_cards drop column if exists project_id;
