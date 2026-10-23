---
name: supabase-patterns
description: service.py에서 Supabase(supabase-py)를 호출하는 코드를 쓰거나 리뷰할 때 쓴다. .single() 예외, service role 키, pgvector/rpc 같은 이 레포 특유의 함정을 다룬다.
---

# Supabase 사용 패턴

이 레포의 DB 접근은 전부 `app/db.py`의 단일 `supabase` 클라이언트를 통한다.
다른 곳에서 `create_client`를 새로 만들지 않는다.

## `.single()`은 결과가 정확히 1건 보장될 때만

```python
result = supabase.table("users").select("*").eq("id", user_id).single().execute()
```

행이 0건이거나 2건 이상이면 예외가 발생한다. PK로 1건 조회하는 경우(`app/domains/users/service.py` 패턴)에만 쓴다.
없을 수도 있는 조회(검색, 필터)는 `.single()`을 쓰지 않고 리스트로 받아서
`result.data`의 길이를 직접 확인한다.

## service role 키 = RLS 무시

`app/db.py`는 service role 키로 클라이언트를 만든다. 모든 테이블이 "RLS 켜짐 + 정책 없음"인 이유가 이거다
(service role은 어차피 RLS를 통과함). 지금은 회원/인증 개념이 없어서 권한 체크가 필요 없지만,
나중에 사용자별 접근 제어가 생기면 DB가 아니라 **이 service role 클라이언트를 쓰는 서비스 코드 안에서**
직접 체크해야 한다는 걸 기억한다.

## 벡터 유사도 검색은 `rpc()`로만

pgvector 유사도 검색(`<=>` 등)은 PostgREST(Supabase SDK)가 지원하지 않는다.
`sql/functions.sql`에 Postgres 함수로 만들고 `supabase.rpc("함수명", {...}).execute()`로 호출한다
(`app/domains/agent/service.py`의 `match_documents` 참고).

현재 DB에는 pgvector 확장이 아직 안 켜져 있다 (뭘 임베딩할지 미정). 기능 구현하면서 확장되면
`query_embedding` placeholder 자리에 실제 임베딩 호출이 들어가는 구조다 — 미리 끌어다 쓰지 않는다.

## DB 자체를 바꿔야 하면

코드에서 새 컬럼/테이블이 필요해도 직접 Supabase에 실행하지 않는다. `sql/tables.sql`(또는
`sql/functions.sql`)에 SQL만 적어서 PR로 올린다. 실제 실행은 팀장이 한다 (자세한 건
`backend-conventions` 스킬의 DB 스키마 항목 참고).
