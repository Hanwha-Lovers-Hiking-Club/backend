---
name: new-domain-scaffold
description: 새 도메인(기능)을 app/domains/ 아래에 추가할 때 쓴다. "OO 도메인 추가해줘", "OO 기능 만들어줘", "새 엔드포인트 추가해줘" 같은 요청에 router/schemas/service 3분할 구조를 규칙대로 만들고 main.py에 등록한다.
---

# 새 도메인 추가하기

도메인마다 `router.py` / `schemas.py` / `service.py` 3개 파일만 만든다.
(규칙 배경은 `backend-conventions` 스킬 참고 — 여기는 실행 체크리스트만.)

## 순서

1. `app/domains/<도메인명>/` 폴더 생성, `__init__.py`는 빈 파일로 둔다.
2. `schemas.py` — 요청/응답 pydantic 모델만 정의. 로직 없음.
3. `service.py` — 진짜 로직(DB 호출 등) 전부 여기. `app.db.supabase`를 가져다 쓴다.
4. `router.py` — 경로 정의만. `service.py` 함수 호출 외 로직 작성 금지.
5. `app/main.py`에 두 줄 추가: `from app.domains.<도메인명>.router import router as <도메인명>_router` / `app.include_router(<도메인명>_router)`.
6. DB 테이블을 새로 쓰거나 바꾸면 `docs/schema.md`를 먼저 확인하고(컬럼명 추측 금지), 테이블이 바뀌면 `sql/tables.sql`도 같은 PR에서 같이 수정한다.

## 고정된 건 역할 분리뿐, 엔드포인트 모양은 기능에 맞게

`router.py`는 항상 이 뼈대로 시작한다 (엔드포인트 종류/파라미터는 기능에 맞게 채운다):
```python
from fastapi import APIRouter

from app.domains.<domain> import service
from app.domains.<domain>.schemas import ...

router = APIRouter(prefix="/<domain>", tags=["<domain>"])
```

`schemas.py`, `service.py`는 빈 파일에서 시작해서 그 기능에 필요한 모델/함수를 채운다.
`app/domains/documents/`, `app/domains/knowledge_cards/`는 지금 이 뼈대(빈 `schemas.py`/`service.py`
+ 위 `router.py`)만 있는 상태다. 각자 자기 기능에 맞게 채우면 된다.

끝나면: `response_model`이 모든 엔드포인트에 있는지, `router.py`에 DB/비즈니스 로직이 새어들어가지 않았는지 확인한다.

## 로직이 커지면 (RAG/LangGraph 등)

3분할 규칙은 "router=경로, schemas=모델, service=로직"이라는 역할 분리이지,
`service.py` 파일 하나에 모든 로직을 다 넣으라는 뜻은 아니다. 그래프 정의, 노드 함수,
프롬프트처럼 `service.py`가 감당하기 벅차지면 같은 도메인 폴더 안에 `graph.py`,
`nodes.py`, `prompts.py` 같은 파일을 추가해서 나눠도 된다. 단, `router.py`는 여전히
`service.py`만 호출하고 그 안의 다른 모듈을 직접 import하지 않는다 — 외부에서 보는
계약(router → service)만 지키면 내부 구성은 자유롭다.
