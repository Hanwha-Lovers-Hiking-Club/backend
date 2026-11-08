-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- 프로젝트 폴더에 보여 줄 표시 정보를 projects에 추가한다.
-- 여러 번 실행해도 안전하다 (if not exists). 기존 컬럼과 다른 테이블은 바꾸지 않는다.
-- 문서에서 확인할 수 없는 프로젝트도 있어서 stack을 뺀 나머지는 NULL을 허용한다.

alter table projects
  add column if not exists domain text
    constraint projects_domain_check
    check (domain in ('ai', 'frontend', 'backend', 'data', 'app')),
  add column if not exists team text,
  add column if not exists period text,
  add column if not exists description text,
  add column if not exists stack text[] not null default '{}';
