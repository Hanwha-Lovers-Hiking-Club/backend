-- 이미 tables.sql을 실행해 테이블이 만들어진 DB에 컬럼을 추가하는 스크립트.
-- tables.sql은 이 변경이 이미 반영된 최종 구조이므로, 새로 만드는 DB에는 이 파일이 필요 없다.
-- add column if not exists 라서 여러 번 실행해도 안전하다.

alter table knowledge_cards add column if not exists keywords     text[] not null default '{}';
alter table knowledge_cards add column if not exists department   text;
alter table knowledge_cards add column if not exists project_date date;

create index if not exists knowledge_cards_keywords_idx on knowledge_cards using gin (keywords);

alter table card_documents add column if not exists location text;
alter table card_documents add column if not exists excerpt  text;
