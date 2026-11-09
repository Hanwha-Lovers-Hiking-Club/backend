-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- 프로젝트 검색에 대표 기술(stack)을 포함하려고 배열을 문자열로 합친 생성 컬럼을 추가한다.
-- stack(text[])은 PostgREST에서 부분 일치로 검색할 수 없어서 이 컬럼을 ilike로 검색한다.
-- 함수 card_array_to_text는 2026-11-05_add_card_search_text.sql에서 이미 만들었다.
-- 생성 컬럼이라 업로드 코드는 바꾸지 않는다. 여러 번 실행해도 안전하다.
--
-- 백엔드 검색 코드가 이 컬럼을 쓰므로, 이 SQL을 먼저 실행한 뒤 PR을 머지한다.
-- 순서가 바뀌면 프로젝트 목록 조회(GET /api/projects)가 실패한다.

alter table projects add column if not exists stack_text text
  generated always as (card_array_to_text(stack)) stored;

-- PostgREST가 새 컬럼을 바로 알아보도록 스키마 캐시를 다시 읽게 한다
notify pgrst, 'reload schema';
