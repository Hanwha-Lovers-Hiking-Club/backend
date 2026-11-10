-- SQL Editor에서 실행. 기존 카드의 횟수는 0, 마지막 시각은 NULL에서 시작한다.
alter table public.knowledge_cards
  add column if not exists view_count bigint not null default 0,
  add column if not exists reference_count bigint not null default 0,
  add column if not exists last_viewed_at timestamptz,
  add column if not exists last_referenced_at timestamptz,
  add column if not exists draft_reasons text[] not null default '{}';

-- 이용 통계만 바뀐 경우 카드 수정일은 유지한다.
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  if (to_jsonb(new) - array['view_count', 'reference_count', 'last_viewed_at', 'last_referenced_at'])
     is distinct from
     (to_jsonb(old) - array['view_count', 'reference_count', 'last_viewed_at', 'last_referenced_at'])
  then
    new.updated_at = now();
  end if;
  return new;
end;
$$;

create or replace function public.record_card_usage(p_card_ids uuid[], p_kind text)
returns void language plpgsql security invoker set search_path = '' as $$
begin
  if p_kind = 'view' then
    update public.knowledge_cards
      set view_count = view_count + 1, last_viewed_at = now()
      where id = any(p_card_ids) and status = 'active';
  elsif p_kind = 'reference' then
    update public.knowledge_cards
      set reference_count = reference_count + 1, last_referenced_at = now()
      where id = any(p_card_ids) and status = 'active';
  else
    raise exception 'invalid card usage kind';
  end if;
end;
$$;

revoke all on function public.record_card_usage(uuid[], text) from public, anon, authenticated;
grant execute on function public.record_card_usage(uuid[], text) to service_role;
