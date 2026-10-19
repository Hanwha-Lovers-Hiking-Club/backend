-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- 한 카드가 같은 문서의 여러 부분을 근거로 할 수 있도록, card_documents의
-- 기본키를 (card_id, document_id) 조합에서 별도 id로 바꾼다.
-- 여러 번 실행해도 안전하다. 기존 행이 있어도 id가 자동으로 채워진다.

alter table card_documents add column if not exists id uuid not null default gen_random_uuid();

alter table card_documents drop constraint if exists card_documents_pkey;
alter table card_documents add primary key (id);

-- 기존 기본키가 card_id 조회를 겸했으므로 별도 인덱스가 필요하다.
create index if not exists card_documents_card_id_idx on card_documents (card_id);
