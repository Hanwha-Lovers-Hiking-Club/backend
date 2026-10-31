<!--
  Supabase에 타입 자동생성이 없어서, 이 문서를 테이블 구조의 "사람이 관리하는 진실"로 둠.
  팀원들의 AI가 pydantic 모델(schemas.py) 짤 때 이 문서를 참고하게 만드는 용도.
  규칙: Supabase SQL Editor에서 테이블 바꾸면, 이 문서도 같은 PR에서 같이 업데이트.
-->

# DB 스키마

생성 SQL은 `sql/tables.sql`. 모든 테이블은 RLS 켜짐 + 정책 없음 (백엔드 service role 키로만 접근).
기존 DB에는 `sql/2026-10-31_document_card_embeddings.sql`,
`sql/2026-10-31_document_jobs.sql`, `sql/functions.sql` 순서로 적용한다.
마지막 파일은 저장 RPC와 Job RPC를 함께 정의한다. 실제 SQL Editor 실행은 팀장 담당이다.
2026-10-31 읽기 전용 확인 결과, 운영 DB에는 네 벡터 컬럼이 이미 `vector(1024)`로 있었고
`document_save_receipts` 및 `save_document_cards` RPC는 없었다. 벡터 인덱스 상태는 별도 확인이 필요하다.
운영 인덱스와 pgvector 버전은 `sql/2026-10-31_inspect_embedding_schema.sql`로 읽기 전용 확인한다.
업로드·Job·SSE는 `sql/2026-10-31_document_jobs.sql`을 기존 DB에 적용한 뒤 사용할 수 있다.
`document_jobs`/`document_job_events`와 두 작업 RPC는 현재 운영 DB에 적용되지 않았다.

## documents

원본 문서 메타. 실제 파일은 Supabase Storage에 있고, 여기에는 위치만 저장.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | PK |
| title | text | 현재 업로드 시 원래 파일명으로 기록 (필수, 빈 문자열 불가) |
| original_filename | text | 사용자가 올린 원래 파일명 (필수). 확장자는 여기서 잘라 응답에 내려준다 |
| size_bytes | bigint | 파일 용량(바이트) (필수, 0 이상) |
| storage_bucket | text | Storage 버킷 이름 (필수) |
| storage_path | text | 버킷 안 파일 경로 (필수). `(storage_bucket, storage_path)` 유니크 |
| created_at | timestamptz | 생성 시각 |

## knowledge_cards

지식카드 본문 + 메타데이터.

| 컬럼 | 타입 | 필수 | 설명 |
|---|---|---|---|
| id | uuid | O | PK |
| title | text | O | 제목 |
| summary | text | O | 핵심 내용 요약 |
| context | text | O | 경험/의사결정의 배경과 상황 |
| problem | text | 조건부 | 발생한 문제 또는 과제 |
| cause | text | 조건부 | 문제의 원인 |
| approach | text | 조건부 | 해결 방법, 기술적 선택, 접근 방식 |
| rationale | text | 조건부 | 접근 방식을 선택한 이유 |
| outcome | text | 조건부 | 적용 후 확인된 결과 |
| lesson | text | O | 재사용 가능한 교훈 |
| technologies | text[] | O (빈 배열 가능) | 기술 스택(WebSocket, Supabase 등). 기본값 `{}` |
| keywords | text[] | O (빈 배열 가능) | 분야/주제어(웹 개발, QA, 네트워크 등). 기본값 `{}` |
| department | text | X | 담당 부서. 식별 불가하면 NULL |
| project_date | date | X | 지식이 나온 프로젝트 시점. 식별 불가하면 NULL |
| project_name | text | X | LLM이 문서에서 추출한 프로젝트 이름. 표기가 달라도(플랜밍고/planmingo) 같은 프로젝트로 묶지 않음. 식별 불가하면 NULL |
| status | text | O | `draft`(기본) / `active` / `flagged` / `archived`. RAG 검색 포함 여부 판단 기준 |
| quality_score | numeric | X | 카드 품질 내부 평가 점수 |
| last_verified_at | timestamptz | X | 마지막 검증 시각 |
| verification_version | text | X | 마지막 검증에 쓴 평가 기준/로직 버전 |
| content_embedding | vector(1024) | X | 카드 전체 맥락의 KURE 벡터 |
| situation_embedding | vector(1024) | X | 상황·문제 관점의 KURE 벡터 |
| solution_embedding | vector(1024) | X | 해결 관점의 KURE 벡터 |
| lesson_embedding | vector(1024) | X | 교훈 관점의 KURE 벡터 |
| created_at | timestamptz | O | 생성 시각 |
| updated_at | timestamptz | O | 수정 시각 (트리거로 자동 갱신) |

제약:
- `title`, `summary`, `context`, `lesson`은 빈 문자열 불가.
- 조건부 5개(`problem`, `cause`, `approach`, `rationale`, `outcome`) 중 최소 1개는 공백 아닌 값이 있어야 함 (CHECK).
- `status`는 text + CHECK. 값이 늘면 제약만 교체하고, pydantic의 Literal/Enum도 같이 수정.
- 현재 저장 정책: Verification Gate를 통과한 카드만 카드·근거·벡터를 한 트랜잭션으로 저장하며,
  커밋과 동시에 `active`로 공개한다. `draft`는 일반 테이블 기본값으로 남긴다.
- 카드의 `source`(근거 문서)는 컬럼이 아니라 `card_documents`로 연결. 카드당 문서 1개 이상은 DB가 아니라 앱 로직(service.py)에서 보장.

네 `vector(1024)` 컬럼은 현재 저장 차원과 맞는다. 검색 RPC와 거리 연산자가 아직 정해지지 않아
벡터 인덱스 DDL은 추가하지 않았다. 초기에는 정확 검색을 사용할 수 있고, 검색량·지연시간이
필요로 할 때 검색 쿼리와 같은 연산자의 HNSW 인덱스를 검토한다. 코사인 거리(`<=>`)를 택하면
`vector_cosine_ops`를 사용한다. 1,024차원은 pgvector의 `vector` HNSW 인덱스 한도인
2,000차원 안에 있다. [pgvector 공식 안내](https://github.com/pgvector/pgvector#hnsw)를 따른다.

## card_documents

지식카드의 근거(카드의 source). 행 하나가 근거 하나이며, 한 카드가 같은 문서의 여러 부분을 근거로 하면 같은 `(card_id, document_id)`로 행이 여러 개 생긴다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | PK |
| card_id | uuid | FK → knowledge_cards.id (삭제 시 cascade) |
| document_id | uuid | FK → documents.id (삭제 시 cascade) |
| location | text | 원문 내 위치. 예: "2장 · 답변 근거와 지식 연결". NULL 허용 |
| excerpt | text | 원문에서 그대로 가져온 근거 발췌문. NULL 허용 |

`location`/`excerpt`는 근거마다 달라서 이 테이블에 둔다. 카드당 근거 1개 이상은 DB가 아니라 앱 로직에서 보장한다.

## document_save_receipts

문서별 카드·근거·벡터 저장을 한 트랜잭션으로 확정하고 같은 요청의 중복 저장을 막는다. `sql/functions.sql`의 `save_document_cards` RPC만 이 테이블과 카드 행을 함께 쓴다.
카드가 0개인 문서도 처리 완료 기록으로 빈 `card_ids` 영수증을 남긴다. 같은 문서 ID와
같은 payload_hash의 재시도는 기존 결과를 반환한다. 내용이 달라진 같은 ID의 재처리는
현재 지원하지 않으며, 새 업로드는 새 문서 ID를 발급해야 한다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| document_id | uuid | PK, FK → documents.id |
| payload_hash | text | 저장 요청의 SHA-256 해시. 같은 문서의 다른 내용 재저장은 거부 |
| card_ids | uuid[] | 확정된 카드 ID 목록. 빈 목록 허용 |
| created_at | timestamptz | 저장 확정 시각 |

## document_jobs / document_job_events

업로드는 Storage 원본과 `documents` 행을 먼저 만들고 `create_document_job` RPC로
작업 및 `queued` 이벤트를 한 트랜잭션에 등록한다. `record_document_job_event` RPC가
상태 갱신과 SSE 이벤트를 함께 기록한다. 두 테이블은 RLS 활성화, 정책 없음,
service role 조회만 허용하며 쓰기는 RPC를 통한다.

| 테이블 | 주요 컬럼 | 설명 |
|---|---|---|
| document_jobs | id uuid, document_ids uuid[], status text, result jsonb, created_at, updated_at | 상태는 queued/running/completed/partial/failed. result는 종료 시 집계 |
| document_job_events | id bigint, job_id uuid, event_type text, payload jsonb, created_at | 재접속용 순번과 이벤트 이력 |

API는 `POST /api/upload`(파일 1~10개, 각 50 MB)로 작업 ID를 발급하고,
`GET /api/jobs/{job_id}`로 상태를 읽으며,
`GET /api/jobs/{job_id}/events`는 `Last-Event-ID` 이후 이벤트를 SSE로 전달한다.
종료 상태를 기록한 뒤 구독한 클라이언트도 이력에서 결과를 받는다.
FastAPI BackgroundTasks 실행 중 프로세스가 종료되면 실행 중인 작업을 자동 재개하지
않으므로 운영 워커·재시도 정책은 후속 작업이다.

## 아직 DB에 없는 것 (필요해지면 추가)

- 카드 유형(`type`): 추출 결과를 보고 분류가 정해지면 `type text` + CHECK 제약으로 추가.
- 문서 요약(`documents.summary`): 필요해지면 추가.
- 중복 업로드 정책: 같은 내용의 카드가 쌓이는 것은 현재 허용. 임베딩 도입 후 카드 유사도로 판별(`status = 'flagged'` 활용) 검토.
- `project_id`: 프로젝트 테이블이 생기면 다시 추가.
