-- Supabase SQL Editor에 붙여넣고 한 번 실행해두는 함수 모음.
-- PostgREST(SDK)는 pgvector 유사도 검색을 지원하지 않아서,
-- 이 파일에 Postgres 함수를 만들어두고 파이썬에서는 supabase.rpc("함수명", {...})로 호출한다.

create extension if not exists vector;
