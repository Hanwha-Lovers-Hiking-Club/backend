-- 기존 지식카드의 project_id를 근거 문서의 프로젝트로 채운다. 한 번만 실행하는 데이터 작업이다.
-- 전제: sql/2026-11-08_add_projects.sql 실행 완료, documents.project_id가 모두 채워져 있음.
-- project_id만 바꾼다. set_updated_at 트리거가 updated_at을 바꾸지 않도록 이 트랜잭션 안에서만 끈다.
-- 중간에 실패하면 트리거 상태를 포함해 전부 되돌아간다.
-- 이미 채워진 카드는 건드리지 않으므로 다시 실행해도 안전하다.

begin;

do $$
begin
  if exists (select 1 from documents where project_id is null) then
    raise exception 'project_id가 빈 문서가 있습니다';
  end if;

  if exists (
    select 1
    from card_documents cd
    join documents d on d.id = cd.document_id
    group by cd.card_id
    having count(distinct d.project_id) > 1
  ) then
    raise exception '근거 문서의 프로젝트가 둘 이상인 카드가 있습니다';
  end if;

  if exists (
    select 1
    from knowledge_cards k
    where not exists (select 1 from card_documents cd where cd.card_id = k.id)
  ) then
    raise exception '근거가 없는 카드가 있습니다';
  end if;
end;
$$;

alter table knowledge_cards disable trigger knowledge_cards_set_updated_at;

update knowledge_cards k
set project_id = m.project_id
from (
  select distinct cd.card_id, d.project_id
  from card_documents cd
  join documents d on d.id = cd.document_id
) m
where k.id = m.card_id
  and k.project_id is null;

alter table knowledge_cards enable trigger knowledge_cards_set_updated_at;

do $$
begin
  if exists (select 1 from knowledge_cards where project_id is null) then
    raise exception 'project_id가 빈 카드가 남아 있습니다';
  end if;
end;
$$;

commit;

-- 확인용: 카드 138, 빈 값 0, 트리거 상태 O(켜짐)가 나와야 한다
select
  (select count(*) from knowledge_cards) as cards,
  (select count(*) from knowledge_cards where project_id is null) as empty_project_id,
  (select tgenabled from pg_trigger where tgname = 'knowledge_cards_set_updated_at') as trigger_state;
