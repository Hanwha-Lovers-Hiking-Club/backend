-- 프로젝트 유형 코드를 개발자·PM·QA가 쓰는 말에 맞춘다.
-- web -> frontend, platform -> backend, client -> app. ai, data는 그대로다.
-- 새 판별 기준으로 유형이 달라지는 프로젝트 3개도 backend로 옮긴다.
-- 여러 번 실행해도 안전하다. 프로젝트 이름과 다른 컬럼은 바꾸지 않는다.

begin;

alter table projects drop constraint if exists projects_domain_check;

update projects
set domain = case domain
  when 'web' then 'frontend'
  when 'platform' then 'backend'
  when 'client' then 'app'
end
where domain in ('web', 'platform', 'client');

update projects
set domain = 'backend'
where name in ('결제 데이터 정합성', '반경길잡이', '곁창고');

alter table projects
  add constraint projects_domain_check
  check (domain in ('ai', 'frontend', 'backend', 'data', 'app'));

commit;

-- 확인: frontend 12, ai 9, backend 8, data 6, app 2 (이후 업로드가 있으면 그만큼 늘어난다)
select domain, count(*) from projects group by domain order by count(*) desc;
