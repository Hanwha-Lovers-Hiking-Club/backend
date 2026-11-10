-- Supabase SQL Editor에서 실행. 기존 카드 데이터는 변경하지 않는다.
create table if not exists public.card_maintenance_findings (
  kind text not null constraint card_maintenance_findings_kind_check
    check (kind in ('duplicate', 'stale', 'unused', 'no_usage')),
  card_id uuid not null references public.knowledge_cards(id) on delete cascade,
  related_card_id uuid not null references public.knowledge_cards(id) on delete cascade,
  source text check (source in ('exact', 'similar')),
  verdict text check (verdict in ('duplicate', 'different', 'uncertain')),
  reason text,
  checked_at timestamptz,
  review_status text not null default 'pending'
    check (review_status in ('pending', 'reviewed', 'dismissed')),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  primary key (kind, card_id, related_card_id),
  constraint card_maintenance_findings_check check (
      (kind in ('stale', 'unused') and card_id = related_card_id and source is null
          and verdict is null and checked_at is not null)
      or (kind = 'no_usage' and card_id = related_card_id and source is null
          and verdict is null and checked_at is null)
      or (kind = 'duplicate' and card_id < related_card_id and source is not null
          and verdict is not null and checked_at is null))
);

alter table public.card_maintenance_findings enable row level security;
revoke all on public.card_maintenance_findings from public, anon, authenticated;
grant select, insert, update, delete on public.card_maintenance_findings to service_role;

create or replace function public.record_card_maintenance_finding(
  p_kind text, p_card_id uuid, p_related_card_id uuid,
  p_source text, p_verdict text, p_reason text, p_checked_at timestamptz
) returns void language sql security invoker set search_path = '' as $$
  insert into public.card_maintenance_findings
    (kind, card_id, related_card_id, source, verdict, reason, checked_at)
  values
    (p_kind, p_card_id, p_related_card_id, p_source, p_verdict, p_reason, p_checked_at)
  on conflict (kind, card_id, related_card_id) do update set
    source = excluded.source,
    verdict = excluded.verdict,
    reason = excluded.reason,
    checked_at = excluded.checked_at,
    last_seen_at = now();
$$;

revoke all on function public.record_card_maintenance_finding(
  text, uuid, uuid, text, text, text, timestamptz
) from public, anon, authenticated;
grant execute on function public.record_card_maintenance_finding(
  text, uuid, uuid, text, text, text, timestamptz
) to service_role;
