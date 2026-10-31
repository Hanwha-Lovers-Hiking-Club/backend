<!--
  Supabase에 타입 자동생성이 없어서, 이 문서를 테이블 구조의 "사람이 관리하는 진실"로 둠.
  팀원들의 AI가 pydantic 모델(schemas.py) 짤 때 이 문서를 참고하게 만드는 용도.
  규칙: Supabase SQL Editor에서 테이블 바꾸면, 이 문서도 같은 PR에서 같이 업데이트.
-->

# DB 스키마

생성 SQL은 `sql/tables.sql`. 모든 테이블은 RLS 켜짐 + 정책 없음 (백엔드 service role 키로만 접근).

## documents

원본 문서 메타. 실제 파일은 Supabase Storage에 있고, 여기에는 위치만 저장.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | PK |
| title | text | 문서 제목. LLM이 문서에서 추출 (필수, 빈 문자열 불가) |
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
| technologies | text[] | O (빈 배열 가능) | 기술 스택. **정규화된 표준 ID로 저장**(`nextjs`, `postgresql`, 사전에 없는 기술은 소문자·구분기호 제거 값). 표기 통일 규칙과 사전은 `app/core/technology/`, 화면 표시명은 API 응답에서 변환. 기본값 `{}` |
| keywords | text[] | O (빈 배열 가능) | 분야/주제어(웹 개발, QA, 네트워크 등). 기본값 `{}` |
| department | text | X | 담당 부서. 식별 불가하면 NULL |
| project_date | date | X | 지식이 나온 프로젝트 시점. 식별 불가하면 NULL |
| project_name | text | X | LLM이 문서에서 추출한 프로젝트 이름. 표기가 달라도(플랜밍고/planmingo) 같은 프로젝트로 묶지 않음. 식별 불가하면 NULL |
| status | text | O | `draft`(기본) / `active` / `flagged` / `archived`. RAG 검색 포함 여부 판단 기준 |
| quality_score | numeric | X | 카드 품질 내부 평가 점수 |
| last_verified_at | timestamptz | X | 마지막 검증 시각 |
| verification_version | text | X | 마지막 검증에 쓴 평가 기준/로직 버전 |
| created_at | timestamptz | O | 생성 시각 |
| updated_at | timestamptz | O | 수정 시각 (트리거로 자동 갱신) |
| content_embedding | vector(1024) | X | 전체 경험 검색용 임베딩 (title~lesson 전체 조합) |
| situation_embedding | vector(1024) | X | 상황·문제 검색용 임베딩 (context, problem 조합) |
| solution_embedding | vector(1024) | X | 해결 경험 검색용 임베딩 (problem~outcome 조합) |
| lesson_embedding | vector(1024) | X | 교훈 검색용 임베딩 (context, problem, lesson 조합) |

차원 1024는 kure-v3(256~2048 지원, 차원을 낮춰도 정확도 손실이 적음)와 pgvector 인덱스 상한선
(2,000차원 초과 시 HNSW/IVFFlat 인덱스 불가)을 함께 고려해 정함. 조합 텍스트 구성은 임베딩 설계
가이드 참고. 인덱스는 아직 안 걺(카드 수/조회 패턴이 쌓이면 추가).

제약:
- `title`, `summary`, `context`, `lesson`은 빈 문자열 불가.
- 조건부 5개(`problem`, `cause`, `approach`, `rationale`, `outcome`) 중 최소 1개는 공백 아닌 값이 있어야 함 (CHECK).
- `status`는 text + CHECK. 값이 늘면 제약만 교체하고, pydantic의 Literal/Enum도 같이 수정.
- 카드의 `source`(근거 문서)는 컬럼이 아니라 `card_documents`로 연결. 카드당 문서 1개 이상은 DB가 아니라 앱 로직(service.py)에서 보장.

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

## 아직 DB에 없는 것 (필요해지면 추가)

- 카드 유형(`type`): 추출 결과를 보고 분류가 정해지면 `type text` + CHECK 제약으로 추가.
- 문서 요약(`documents.summary`): 필요해지면 추가.
- 중복 업로드 정책: 같은 내용의 카드가 쌓이는 것은 현재 허용. 임베딩 도입 후 카드 유사도로 판별(`status = 'flagged'` 활용) 검토.
- `project_id`: 프로젝트 테이블이 생기면 다시 추가.
