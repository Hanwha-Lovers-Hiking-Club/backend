-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- 업로드하자마자 프로젝트 행이 임시 이름으로 생겨서, 분석이 끝나기 전(이름·설명·대표 기술 없음)이나
-- 분석이 실패한 프로젝트도 목록에 보였다. 분석이 성공으로 끝난 시각을 저장하고,
-- 프로젝트 목록(GET /api/projects)은 이 값이 있는 프로젝트만 보여 준다.
-- 기존 프로젝트는 모두 분석이 끝난 것으로 보고 생성 시각으로 채운다. 여러 번 실행해도 안전하다.
--
-- 백엔드 목록 코드가 이 컬럼을 쓰므로, 이 SQL을 먼저 실행한 뒤 PR을 머지한다.
-- 순서가 바뀌면 프로젝트 목록 조회가 실패한다.

alter table projects add column if not exists completed_at timestamptz;

update projects set completed_at = created_at where completed_at is null;

-- PostgREST가 새 컬럼을 바로 알아보도록 스키마 캐시를 다시 읽게 한다
notify pgrst, 'reload schema';
