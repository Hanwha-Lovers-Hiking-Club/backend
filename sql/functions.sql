-- Supabase SQL Editor에 붙여넣고 한 번 실행해두는 함수 모음.
-- 기존 match_documents 예시는 documents에 없는 content/embedding 컬럼을
-- 참조하므로 제거했다. 카드 검색 RPC는 검색 담당 범위에서 정의한다.

create extension if not exists vector;

-- 검증을 통과한 한 문서의 카드·근거·벡터·완료 영수증을 원자적으로 저장한다.
-- 동일한 document_id/payload_hash 재호출은 기존 카드 ID를 돌려준다.
-- 다른 payload_hash로 같은 문서를 다시 저장하려면 별도 재처리 정책이 필요하다.
-- p_cards=[]도 처리 완료로 기록하며 빈 card_ids 영수증을 반환한다.
-- pass 카드만 입력되고 active 공개는 이 트랜잭션이 커밋될 때 시작한다.
create or replace function save_document_cards(
  p_document_id uuid,
  p_payload_hash text,
  p_cards jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_inserted integer;
  v_receipt document_save_receipts%rowtype;
  v_card jsonb;
  v_evidence jsonb;
  v_card_id uuid;
  v_card_ids uuid[] := '{}'::uuid[];
begin
  if p_payload_hash !~ '^[0-9a-f]{64}$' or jsonb_typeof(p_cards) is distinct from 'array' then
    raise exception 'Invalid document save request';
  end if;
  perform 1 from documents where id = p_document_id for update;
  if not found then
    raise exception 'Source document does not exist';
  end if;

  insert into document_save_receipts (document_id, payload_hash)
  values (p_document_id, p_payload_hash)
  on conflict (document_id) do nothing;
  get diagnostics v_inserted = row_count;
  if v_inserted = 0 then
    select * into v_receipt from document_save_receipts
    where document_id = p_document_id for update;
    if v_receipt.payload_hash is distinct from p_payload_hash then
      raise exception 'Document was already saved with different content';
    end if;
    return jsonb_build_object(
      'document_id', p_document_id, 'payload_hash', p_payload_hash,
      'committed', true, 'result_recorded', true, 'card_ids', to_jsonb(v_receipt.card_ids)
    );
  end if;

  for v_card in select value from jsonb_array_elements(p_cards) loop
    if jsonb_typeof(v_card->'evidences') is distinct from 'array'
       or jsonb_array_length(v_card->'evidences') = 0 then
      raise exception 'Every saved card requires source evidence';
    end if;
    insert into knowledge_cards (
      title, summary, context, problem, cause, approach, rationale, outcome, lesson,
      technologies, keywords, status, content_embedding, situation_embedding,
      solution_embedding, lesson_embedding
    ) values (
      v_card->>'title', v_card->>'summary', v_card->>'context', v_card->>'problem',
      v_card->>'cause', v_card->>'approach', v_card->>'rationale', v_card->>'outcome',
      v_card->>'lesson',
      array(select jsonb_array_elements_text(v_card->'technologies')),
      array(select jsonb_array_elements_text(v_card->'keywords')),
      'active',
      (v_card->>'content_embedding')::vector(1024),
      (v_card->>'situation_embedding')::vector(1024),
      (v_card->>'solution_embedding')::vector(1024),
      (v_card->>'lesson_embedding')::vector(1024)
    ) returning id into v_card_id;
    v_card_ids := array_append(v_card_ids, v_card_id);
    for v_evidence in select value from jsonb_array_elements(v_card->'evidences') loop
      insert into card_documents (card_id, document_id, location, excerpt)
      values (v_card_id, p_document_id, v_evidence->>'location', v_evidence->>'excerpt');
    end loop;
  end loop;

  update document_save_receipts set card_ids = v_card_ids where document_id = p_document_id;
  return jsonb_build_object(
    'document_id', p_document_id, 'payload_hash', p_payload_hash,
    'committed', true, 'result_recorded', true, 'card_ids', to_jsonb(v_card_ids)
  );
end;
$$;

revoke all on function save_document_cards(uuid, text, jsonb) from public, anon, authenticated;
grant execute on function save_document_cards(uuid, text, jsonb) to service_role;
