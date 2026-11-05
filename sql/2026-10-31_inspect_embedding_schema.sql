-- Supabase SQL Editor에서 읽기 전용으로 실행한다. 데이터나 스키마를 변경하지 않는다.
-- 결과를 확인한 뒤 검색 담당과 인덱스 연산자/적용 시점을 결정한다.

select extname, extversion
from pg_extension
where extname = 'vector';

select a.attname as column_name, format_type(a.atttypid, a.atttypmod) as column_type
from pg_attribute a
where a.attrelid = to_regclass('public.knowledge_cards')
  and a.attname in (
    'content_embedding', 'situation_embedding', 'solution_embedding', 'lesson_embedding'
  )
  and a.attnum > 0
  and not a.attisdropped
order by a.attname;

select indexname, indexdef
from pg_indexes
where schemaname = 'public'
  and tablename = 'knowledge_cards'
order by indexname;

select
  to_regclass('public.document_save_receipts') as receipt_table,
  to_regprocedure('public.save_document_cards(uuid,text,jsonb)') as save_function;
