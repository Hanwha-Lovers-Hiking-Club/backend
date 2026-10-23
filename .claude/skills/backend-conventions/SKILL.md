---
name: backend-conventions
description: 이 레포(FastAPI + Supabase 백엔드)에서 코드를 쓰거나 리뷰할 때 항상 지켜야 할 팀 공통 규칙. 네이밍, 커밋/브랜치, 도메인 구조, DB 스키마 동기화 규칙을 담는다.
---

# 백엔드 공통 규칙

이 레포를 수정할 때는 아래를 항상 지킨다. 바뀌어야 한다고 판단되면 이 파일을 고치지 말고 팀에 먼저 제안한다 (이 파일은 팀 합의 사항).

## 아키텍처: 도메인 3분할

기능 하나 = `app/domains/<도메인명>/` 폴더 하나. 기본 구성은 역할이 고정된 파일 3개다 (이건 최소
구성 예시고, 그 이상은 막는 규칙이 아니다):

- `router.py` — URL 경로 정의만. 로직은 절대 여기 쓰지 않고 `service.py` 함수를 호출만 한다.
- `schemas.py` — 요청/응답 모양(pydantic 모델)만 정의.
- `service.py` — 진짜 로직(DB 호출, LLM 호출, 비즈니스 규칙)이 사는 곳.

고정된 건 이 3가지 역할의 분리뿐이다. 그 역할들을 유지하는 한, 도메인 폴더 내부에 파일을
더 추가하는 건 자유다 (로직이 커져서 `graph.py`/`prompts.py`로 나누는 경우든, 기획 문서처럼
코드가 아닌 파일이든) — 담당 팀원이 그 도메인에 맞게 구성한다.

새 도메인을 추가하는 절차/체크리스트는 `new-domain-scaffold` 스킬을 따른다. 도메인별로 더
구체적인 규칙이 생기면 해당 도메인 폴더 안 `.claude/skills/`에 별도로 둔다.

## 네이밍

- 파일/함수/변수: `snake_case`
- 클래스/pydantic 모델: `PascalCase`
- 환경변수: `UPPER_SNAKE_CASE`

## API 응답

- 모든 엔드포인트는 `response_model`로 응답 모양을 고정한다 (타입 추측 금지).
- 에러 응답은 FastAPI 기본 포맷 그대로 쓴다. 커스텀 에러 포맷은 만들지 않는다 (필요해지면 그때 논의).

## 커밋 / 브랜치

- 커밋 메시지: `타입: 작업 내용` (`feat`, `fix`, `chore`)
- 브랜치 이름: `도메인명/작업내용` (예: `agent/add-ask-endpoint`)

## DB 스키마

- 컬럼명/제약을 추측하지 말고 **항상 `docs/schema.md`를 먼저 읽고** 그대로 따른다.
- 테이블 생성 SQL은 `sql/tables.sql`, 함수는 `sql/functions.sql`. 실제 Supabase SQL Editor 실행은 팀장만 한다 — 팀원은 이 파일에 SQL을 적어서 PR로 올리고, 반영(실행)은 팀장이 한다.
- 테이블을 바꾸면 `sql/tables.sql`과 `docs/schema.md`를 **같은 PR**에서 같이 수정한다. 둘 중 하나만 고치는 PR은 리뷰에서 반려한다.
- 모든 테이블은 RLS 켜짐 + 정책 없음. 백엔드는 service role 키로만 접근하며, 이 키는 절대 프론트에 노출하지 않는다.
