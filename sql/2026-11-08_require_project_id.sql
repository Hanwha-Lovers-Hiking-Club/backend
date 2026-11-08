-- 이미 테이블이 만들어진 DB에 적용하는 스크립트. tables.sql은 이 변경이 반영된 최종 구조다.
-- 문서와 카드가 항상 프로젝트에 속하도록 project_id의 NULL을 막는다.
-- 전제: 2026-11-08_backfill_card_project_id.sql 실행 완료, 빈 project_id가 없음.
-- 빈 값이 남아 있으면 실패하고 전부 되돌아간다. 여러 번 실행해도 안전하다.

begin;

alter table documents alter column project_id set not null;
alter table knowledge_cards alter column project_id set not null;

commit;

-- 확인용: 두 행 모두 is_nullable이 NO로 나와야 한다
select table_name, is_nullable
from information_schema.columns
where table_schema = 'public'
  and column_name = 'project_id'
  and table_name in ('documents', 'knowledge_cards');
