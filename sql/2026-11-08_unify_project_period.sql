-- 프로젝트 기간 표기를 YYYY.MM – YYYY.MM 하나로 통일한다.
-- 형식이 맞지 않는 행만 바꾸므로 여러 번 실행해도 안전하다. period 말고 다른 컬럼은 바꾸지 않는다.
-- 끝이 빈 기존 행의 종료 월은 원문에서 확인한 값이 아니라 시연용 값이다.

begin;

-- 끝이 빈 행은 지정한 시연용 값으로 채운다
update projects as p
set period = v.period
from (values
  ('환불 에이전트', '2026.04 – 2026.08'),
  ('정비 도우미', '2026.06 – 2026.09'),
  ('PR 리뷰 제안 에이전트', '2026.05 – 2026.08'),
  ('조항지기', '2026.07 – 2026.10'),
  ('근거등대', '2026.04 – 2026.08'),
  ('관제 대시보드', '2026.05 – 2026.09'),
  ('웹 성능 개선', '2026.06 – 2026.08'),
  ('함께초안', '2026.05 – 2026.09'),
  ('결제 데이터 정합성', '2026.05 – 2026.08'),
  ('예약 재고 동시성', '2026.07 – 2026.10'),
  ('경계문서함', '2026.06 – 2026.09'),
  ('폴더이음', '2026.04 – 2026.08')
) as v (name, period)
where p.name = v.name
  and p.period ~ '^\d{4}\.\d{2} –$';

-- 목록에 없는데 끝이 빈 행은 종료를 알 수 없으니 비운다
update projects
set period = null
where period ~ '^\d{4}\.\d{2} –$';

-- 2026.05 – 07 -> 2026.05 – 2026.07
update projects
set period = regexp_replace(period, '^(\d{4})\.(\d{2}) – (\d{2})$', '\1.\2 – \1.\3')
where period ~ '^\d{4}\.\d{2} – \d{2}$';

-- 2026.05 -> 2026.05 – 2026.05
update projects
set period = period || ' – ' || period
where period ~ '^\d{4}\.\d{2}$';

commit;

-- 확인: 결과가 0행이어야 한다
select name, period
from projects
where period is not null
  and period !~ '^\d{4}\.\d{2} – \d{4}\.\d{2}$';
