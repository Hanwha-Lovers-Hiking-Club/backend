-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- keywords/technologies(text[])는 PostgREST에서 부분 일치로 검색할 수 없어서,
-- 배열을 문자열로 합친 생성 컬럼을 추가하고 이 컬럼을 ilike로 검색한다.
-- 생성 컬럼은 배열이 바뀌면 DB가 자동으로 갱신하므로, 카드를 저장하는 코드는 바꾸지 않는다.
-- 여러 번 실행해도 안전하다.

-- array_to_string은 immutable이 아니라서 생성 컬럼에 바로 쓸 수 없다.
-- 구분자가 줄바꿈이어서 서로 다른 두 원소에 걸쳐 검색어가 일치하지 않는다.
create or replace function card_array_to_text(arr text[])
returns text
language sql
immutable
as $$ select pg_catalog.array_to_string(arr, E'\n') $$;

alter table knowledge_cards add column if not exists keywords_text text
  generated always as (card_array_to_text(keywords)) stored;
alter table knowledge_cards add column if not exists technologies_text text
  generated always as (card_array_to_text(technologies)) stored;

-- PostgREST가 새 컬럼을 바로 알아보도록 스키마 캐시를 다시 읽게 한다
notify pgrst, 'reload schema';
