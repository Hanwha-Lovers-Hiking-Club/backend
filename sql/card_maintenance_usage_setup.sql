-- 기존 점검 테이블에 미사용 및 사용 이력 없음 유형을 추가한다.
-- 기존 결과와 처리 상태는 유지된다. card_usage_setup.sql 적용 후 실행한다.
begin;
alter table public.card_maintenance_findings
  drop constraint if exists card_maintenance_findings_kind_check,
  drop constraint if exists card_maintenance_findings_check;
alter table public.card_maintenance_findings
  add constraint card_maintenance_findings_kind_check
    check (kind in ('duplicate', 'stale', 'unused', 'no_usage')),
  add constraint card_maintenance_findings_check check (
      (kind in ('stale', 'unused') and card_id = related_card_id and source is null
          and verdict is null and checked_at is not null)
      or (kind = 'no_usage' and card_id = related_card_id and source is null
          and verdict is null and checked_at is null)
      or (kind = 'duplicate' and card_id < related_card_id and source is not null
          and verdict is not null and checked_at is null)
  );
commit;
