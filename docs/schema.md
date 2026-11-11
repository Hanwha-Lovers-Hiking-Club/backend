<!--
  Supabase에 타입 자동생성이 없어서, 이 문서를 테이블 구조의 "사람이 관리하는 진실"로 둠.
  팀원들의 AI가 pydantic 모델(schemas.py) 짤 때 이 문서를 참고하게 만드는 용도.
  규칙: Supabase SQL Editor에서 테이블 바꾸면, 이 문서도 같은 PR에서 같이 업데이트.
-->

# DB 스키마

생성 SQL은 `sql/tables.sql`. 모든 테이블은 RLS 켜짐 + 정책 없음 (백엔드 service role 키로만 접근).

## projects

문서와 지식카드를 묶는 프로젝트. 업로드 1회가 프로젝트 1개다. 마이그레이션은 `sql/2026-11-08_add_projects.sql`, 표시 정보 컬럼은 `sql/2026-11-08_add_project_meta.sql`, 유형 코드 변경은 `sql/2026-11-08_rename_project_domains.sql`, 기간 표기 통일은 `sql/2026-11-08_unify_project_period.sql`, 검색용 생성 컬럼은 `sql/2026-11-09_add_project_search_text.sql`, 분석 완료 시각은 `sql/2026-11-11_add_project_completed_at.sql`.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | PK. 프론트는 이름이 아니라 이 값으로 그룹화한다 |
| name | text | 프로젝트 이름 (필수, 빈 문자열 불가). 업로드 때 파일명 오름차순 첫 파일의 이름으로 만들고 extract가 찾으면 바꾼다. 유니크 아님: 같은 이름의 프로젝트도 별개로 유지한다 |
| domain | text | 분야. `ai` / `frontend` / `backend` / `data` / `app` (CHECK). 폴더와 카드의 색·아이콘을 정한다. NULL이면 미분류 |
| team | text | 담당 팀. 업로드 때 문서에서 뽑고 원문에 없으면 NULL |
| period | text | 문서에서 확인되는 활동 기간. 형식은 `YYYY.MM – YYYY.MM`이고 연도나 월이 같아도 줄이지 않는다 (예: `2026.05 – 2026.07`, `2026.09 – 2026.09`). 문서에 프로젝트 기간이 명시돼 있으면(예정 기간 제외) 그 기간, 없으면 문서에 기록된 첫 활동 월부터 마지막 활동 월까지다. 진행 중인 프로젝트도 채우며, 끝 월은 종료 월이 아니라 마지막으로 기록된 활동 월이다. 시작과 종료를 모두 원문에서 확인하지 못하면 NULL |
| description | text | 프로젝트 한 줄 설명. 업로드 때 LLM이 문서를 요약한 값이며 NULL 허용 |
| stack | text[] | 대표 기술 (빈 배열 가능). 업로드 때 active 카드의 기술을 많이 나온 순으로 5개까지 모은다. `knowledge_cards.technologies`와 달리 화면 표기 그대로 저장한다. 기본값 `{}` |
| stack_text | text | `stack`을 줄바꿈으로 합친 생성 컬럼(자동 생성). 프로젝트 검색(이름·설명·팀과 함께 부분일치) 전용이며 직접 저장하지 않고 응답에도 내려주지 않음 |
| created_at | timestamptz | 생성 시각 |
| completed_at | timestamptz | 업로드한 문서의 분석이 성공으로 끝난 시각. NULL이면 분석 중이거나 실패(서버 재시작으로 끊긴 경우 포함)한 프로젝트이고, 프로젝트 목록에서 빠진다. 상세 조회는 막지 않는다. 응답에는 내려주지 않음 |

## documents

원본 문서 메타. 실제 파일은 Supabase Storage에 있고, 여기에는 위치만 저장.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | PK |
| project_id | uuid | FK → projects.id (필수) |
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
| technologies_text | text | 자동 생성 | `technologies`를 줄바꿈으로 합친 생성 컬럼. 부분 일치 검색 전용이며 직접 저장하지 않고 응답에도 내려주지 않음 |
| keywords_text | text | 자동 생성 | `keywords`를 줄바꿈으로 합친 생성 컬럼. 부분 일치 검색 전용이며 직접 저장하지 않고 응답에도 내려주지 않음 |
| department | text | X | 담당 부서. 식별 불가하면 NULL |
| project_date | date | X | 지식이 나온 프로젝트 시점. 식별 불가하면 NULL |
| project_name | text | X | 더 이상 쓰지 않음. 프로젝트 이름은 `projects.name`에서 읽는다. 값을 저장하지 않으며 이전 검증 후 삭제 예정 |
| project_id | uuid | O | FK → projects.id. 근거 문서와 같은 프로젝트다 |
| status | text | O | `draft`(기본) / `active` / `flagged` / `archived`. RAG 검색 포함 여부 판단 기준 |
| quality_score | numeric | X | 카드 품질 내부 평가 점수 |
| last_verified_at | timestamptz | X | 마지막 검증 시각 |
| verification_version | text | X | 마지막 검증에 쓴 평가 기준/로직 버전 |
| view_count / reference_count | bigint | O | 상세 API 열람 횟수 / 챗봇 참조 카드 반환 횟수. 기존 데이터는 0부터 시작 |
| last_viewed_at / last_referenced_at | timestamptz | X | 마지막 상세 열람 / 챗봇 참조 시각 |
| draft_reasons | text[] | O | 신규 draft 카드의 기존 검증 사유. 기존 카드와 active 카드는 빈 배열 |
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
- `technologies_text`, `keywords_text`는 함수 `card_array_to_text`로 DB가 자동 계산. 마이그레이션은 `sql/2026-11-05_add_card_search_text.sql`.

## card_maintenance_findings

주기 점검 결과의 최신 판단. 설치는 `sql/card_maintenance_setup.sql`을 SQL Editor에서 실행한다.
RLS를 켜고 service role만 접근한다. 카드의 공개 상태는 바꾸지 않는다.

| 컬럼 | 설명 |
|---|---|
| kind | `duplicate`(카드 쌍), `stale`(재검토 시기 도래), `unused`(180일 이상 사용 이력 없음), `no_usage`(기록된 사용 이력 없음) |
| card_id, related_card_id | 카드 FK. 중복은 ID 오름차순, 오래된 카드는 둘 다 같은 ID |
| source | 중복 후보 발견 방식: `exact` 또는 `similar` |
| verdict, reason | LLM의 최신 판단(`duplicate` / `different` / `uncertain`)과 이유. 오래된 카드는 NULL |
| checked_at | `stale`은 마지막 검증일 또는 생성일, `unused`는 마지막 사용 시각. 그 외 NULL |
| review_status | 수동 관리 상태 `pending` / `reviewed` / `dismissed`. 재발견해도 유지 |
| first_seen_at, last_seen_at | 최초 및 마지막 발견 시각. 같은 후보는 새 행 대신 최신 결과 갱신 |

복합 기본키는 `(kind, card_id, related_card_id)`다. 더 이상 후보로 발견되지 않은 행도 남으므로
`last_seen_at`은 최종 관측 시각이지 현재 유효함을 뜻하지 않는다. LLM 판정 실패는 저장하지 않는다.
카드 본문이 변경돼도 수동 `review_status`는 자동 초기화되지 않는다.
사용 이력이 기록되지 않은 카드는 기간을 추정하지 않고 `no_usage`로 기록한다. 기존 설치에는
`sql/card_maintenance_usage_setup.sql`을 적용한다.

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

## document_chunks

문서를 잘라 만든 청크와 그 임베딩. 챗봇이 지식카드에 없는 원문 내용을 검색할 때 쓴다(카드가 안 만들어진
문서도 검색 가능). 문서 1개에 청크 N개. 카드와 직접 연결되지 않는다(카드의 근거는 `card_documents`).

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | uuid | PK |
| document_id | uuid | FK → documents.id (삭제 시 cascade) |
| chunk_index | integer | 문서 안에서의 순서 (0부터, 0 이상). `(document_id, chunk_index)` 유니크 |
| location | text | 원문 내 위치. 예: "2장 · 답변 근거와 지식 연결". NULL 허용 |
| content | text | 청크 원문 (필수, 빈 문자열 불가) |
| embedding | vector(1024) | kure-v3 1024차원 (`knowledge_cards` 임베딩과 같은 모델/차원). NULL 허용 |
| created_at | timestamptz | 생성 시각 |

차원/인덱스 방침은 `knowledge_cards` 임베딩과 같다(벡터 인덱스는 아직 안 걺). `card_documents.excerpt`는
카드가 근거로 골라 쓴 문장이고, `document_chunks.content`는 문서 전체를 빠짐없이 자른 조각이다. 파이프라인의
`Chunk.id`(`<document_id>:<index>`)에서 뒤의 숫자가 `chunk_index`다. 마이그레이션은
`sql/2026-10-30_add_document_chunks.sql`(이 전에 업로드된 문서에는 청크가 없음).

## 아직 DB에 없는 것 (필요해지면 추가)

- 카드 유형(`type`): 추출 결과를 보고 분류가 정해지면 `type text` + CHECK 제약으로 추가.
- 문서 요약(`documents.summary`): 필요해지면 추가.
- 중복 업로드 정책: 같은 내용의 카드가 쌓이는 것은 현재 허용. 임베딩 도입 후 카드 유사도로 판별(`status = 'flagged'` 활용) 검토.
