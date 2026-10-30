# 팀 연결 계약 확인서

작성 기준: 2026-10-28 현재 로컬 코드, `docs/schema.md`, 사용자 확정 요구.
이 문서는 팀 규칙을 새로 정하는 문서가 아니라 **아직 합의하지 않은 연결 계약의 확인 목록**이다.
아래 제안 이름/예시는 팀 공용 API·DB·서비스의 확정 계약으로 사용하지 않는다.
모든 경로는 저장소 루트 기준이다. 설계 배경은 [memo.md](memo.md), 진행은 [checklist.md](checklist.md)를 본다.

## C01. 업로드 결과 → 파이프라인 입력

- 맞출 담당: documents 상위 처리 담당과 pipeline 담당.
- 보내는 파일: `app/domains/documents/service.py`, `repository.py`, `schemas.py`.
- 받는 파일: `pipeline/document.py`의 DocumentSource, `state.py`, `graph.py` 및 초기화 노드.
- 현재 내부 계약: 문서마다 document_id(UUID), original_filename, 로컬 path가 필요하다.
  파이프라인이 임의 문서 DB 행을 만들지 않는다. 같은 문서의 재시도에서는 같은 document_id를 유지한다.
- 확인할 사항:
  1. service가 Storage 다운로드 후 임시 로컬 파일을 제공할지, bytes/Storage 참조 어댑터를 추가할지.
  2. 임시 파일 생성·삭제 시점과 소유자. 작업/재시도/검토 중 원본이 먼저 삭제되지 않도록 할 것.
  3. 문서별 오류와 파일 배치 오류 구분. 수량·총 용량·허용 형식 제한과 서버 동시 실행 상한.
  4. 실행 함수명, async 호출 방식, 작업 ID와 재시작 시 동일 작업을 구분하는 방법.
- 완료 기준: 실제 업로드 결과로 DocumentSource를 만들고 Parse를 실행하는 통합 테스트.

## C02. 문서 제목과 원본 식별

- 맞출 담당: documents 담당, 공용 스키마 문서 담당, 프론트 담당.
- 파일: `documents/repository.py`, `documents/schemas.py`, `pipeline/document.py`,
  `docs/schema.md`의 documents.title/original_filename, 필요 시 `sql/tables.sql`.
- 사용자 확정 요구: 문서 제목은 확장자를 포함한 원래 파일명을 그대로 보존한다.
  생성 지식카드의 title과는 다른 필드다.
- 표현 차이: 현재 docs/schema.md는 documents.title을 'LLM이 문서에서 추출'이라고 설명한다.
  공용 설명을 원래 파일명 정책과 어떻게 맞출지 확인해야 한다.
- 확인할 사항: 응답의 확장자 별도 필드, 같은 이름 파일의 구분(문서 ID 사용), 저장 경로와 표시 이름의 분리.
- 완료 기준: 원래 파일명/확장자 보존 테스트와 공용 문서의 표현 합의. 이번 작업에서 공용 스키마는 변경하지 않는다.

## C03. 파이프라인 결과·진행 → 백그라운드 작업/SSE

- 맞출 담당: documents service/router 담당과 프론트 담당.
- 파일: `pipeline/graph.py`, `pipeline/state.py`, `documents/service.py`, `router.py`, `schemas.py`.
- 명세상 유지 대상: POST /api/upload, GET /api/jobs/{job_id}/events.
  현재 documents 라우터는 /documents prefix만 있고 업로드/SSE 엔드포인트는 구현되지 않았다.
- 2026-10-30 내부 후속: processing.py/state.py의 청크 진행·문서 요약·처리 오류를 추가했다.
  문서의 failed/incomplete/needs_review/ready_to_save/completed를 구분하며 SSE 이름을 정한 것은 아니다.
  청크별 체크포인트 재개를 사용하고, 한 파일/청크의 처리 오류가 다른 문서 결과를 지우지 않도록 했다.
  최종 job 결과를 따로 저장한 뒤 document_lifecycle.release_completed_checkpoints를 누가 호출할지 합의해야 한다.
- 확인할 사항:
  1. job_id 발급·작업 등록·실행·구독의 책임. SSE 연결이 작업 실행 트리거가 되지 않도록 할 것.
  2. stage/progress/completed/failed 등 이벤트 이름과 JSON 필드, 이벤트 ID·재연결·늦은 구독 처리.
  3. 전체 문서 수, 초안 수, 검증 통과 수, 검토 대기 수, 실제 DB 저장 수를 구분하는 최종 결과.
  4. 카드 0개, 일부 파일 실패, 모델 오류, 취소·시간 초과의 최종 작업 상태.
  5. 작업 상태의 메모리/DB 저장 선택과 서버 재시작 복구. LangGraph 개발 서버 체크포인트는 제품 job 저장소가 아니다.
  6. SSE 스트림 시작 이후 실패와 팀 response_model 규칙의 적용 방식.
- 완료 기준: 재연결해도 재실행되지 않고, 늦은 구독도 종료 결과를 받으며, 없는 작업은 404인 통합 테스트.

## C04. 검증 판정 → 카드 상태·검토 대기 보관

- 맞출 담당: pipeline, knowledge_cards, DB/프론트 담당.
- 파일: `pipeline/verification/models.py`, `gate.py`, `pipeline/nodes/save.py`,
  `knowledge_cards/schemas.py`, `service.py`, `docs/schema.md`, 필요 시 `sql/tables.sql`.
- 현재 계약: Gate 판정은 pass/retry/reject/needs_review다.
  DB 카드 상태는 draft/active/flagged/archived이며 needs_review를 DB 상태로 추가하지 않았다.
  verified_drafts도 status=draft를 유지하고 실제 저장/활성화하지 않는다.
- 확인할 사항:
  1. pass 카드의 active 공개 시점. 카드·근거·필요한 벡터 저장이 모두 끝나기 전 공개할지 여부.
  2. needs_review 결과를 별도 작업 결과/테이블/flagged 카드 중 어디에 보관할지와 검토 주체.
  3. reject와 생성 생략 사유의 보존 기간 및 사용자 표시. 통과 수에 포함하지 않을 것.
  4. 규칙 모드가 의미를 확인하지 못해 보류한 결과의 검토/LLM 재검증 경로.
- 완료 기준: 검색 가능한 카드와 보류 결과가 분리되고, 실패 카드를 성공으로 집계하지 않는 테스트.

## C05. 실제 지식카드 저장 서비스

- 맞출 담당: knowledge_cards 담당과 pipeline 담당.
- 호출 파일: `pipeline/nodes/save.py`. 구현 파일: `app/domains/knowledge_cards/service.py`.
  조회·API 응답은 `knowledge_cards/router.py`, `schemas.py`와 호환되어야 한다.
- 현재 상태: list_cards 조회 함수는 있지만 실제 생성/저장 함수는 없다.
  `pipeline/fake_function.py`는 실제 생성 수 0과 is_fake=True를 반환하는 임시 함수일 뿐이다.
- 2026-10-30 내부 후속: nodes/save.py에 선택적 어댑터 경계만 추가했다. 기본값은 미연결이다.
  graph.build_graph(document_saver=...)에 주입하는 호출자는 DocumentSaveRequest를 받아 SaveReceipt를 반환한다.
  카드·근거·필요한 벡터·문서 완료 기록을 저장했다는 확인 전에는 임시 청크/결과를 지우지 않는다.
  실제 함수 계약을 이 내부 모델로 강제한 것은 아니며 어댑터에서 변환한다.
  payload_hash는 동일 요청 재사용을 돕지만 job/document 기반의 영속 멱등 키를 대신하지 않는다.
- 확인할 입력: 단건/배치, 기존 카드 본문 필드, 문서·근거 목록, 내부 필드별 근거/사실 상태,
  관점별 벡터, 작업·경험 단위 멱등 키를 어떤 형태로 받을지.
- 확인할 반환: 저장된 카드 ID, 신규 생성/기존 결과 재사용 수, 항목별 실패, 재시도 가능 여부.
  '함수 이름/입력 클래스/반환 클래스/예외/async 여부'를 명시해야 한다.
- 원자성: 카드만 생성되고 근거/벡터가 실패한 경우의 롤백 또는 복구 책임과 트랜잭션 경계.
  service가 RPC를 사용할지, 테이블 직접 접근은 어느 계층에서 수행할지 확인한다.
- 중복 방지: 동일 job/경험을 재저장할 때 동일 ID를 반환하는지. 현재 experience_id는 내용 fingerprint이며
  모델 출력이 바뀌어도 동일한 DB 저장을 보장하는 영속 멱등 키가 아니다.
- 완료 기준: 실제 서비스 주입, 가짜/실제 선택의 명시성, 부분 실패와 같은 요청 재시도 통합 테스트.

## C06. 필드별 근거·사실 상태 영속 저장

- 맞출 담당: DB 담당, knowledge_cards 담당, pipeline 담당.
- 파일: `pipeline/models.py`, `verification/models.py`, `nodes/save.py`,
  `knowledge_cards/service.py`, `docs/schema.md`, `sql/tables.sql` 또는 `sql/functions.sql`.
- 기존 구조: documents, knowledge_cards, card_documents. card_documents는 document_id/location/excerpt를 보관한다.
  같은 카드가 같은 문서의 여러 부분을 근거로 쓰는 여러 행은 허용된다.
- 미정: field → evidence_refs 배열, evidence_index, 사실 status, lesson_status를 별도 테이블/JSONB 중 어디에 둘지.
  내부 evidence_id/block_id와 card_documents.id 사이의 매핑 및 스키마 버전도 정해야 한다.
- 확인할 사항: 선택적 문자 구간, 탭/줄바꿈을 포함한 원문 발췌 보존, 문서 변경 시 근거 무효화,
  어떤 메타데이터를 API에 노출할지와 기존 API 호환성.
- 완료 기준: 저장 후 원래 카드 필드에서 원본 위치까지 복원하는 테스트.
  DB 변경 시 SQL과 docs/schema.md를 같은 PR에서 수정하며 원격 실행은 팀장이 수행한다.

## C07. 네 관점 임베딩 저장과 검색 연결

- 맞출 담당: pipeline 담당, DB 담당, 챗봇 검색 담당.
- 파일: `pipeline/embedding_texts.py`, `embeddings/`, `nodes/build_embedding_texts.py`, `nodes/embed.py`, `llm.py`, `state.py`,
  `nodes/save.py`, `knowledge_cards/service.py`, `agent/service.py`, `sql/functions.sql`, `docs/schema.md`.
- 확정: 카드 하나에 content/situation/solution/lesson 최대 네 벡터. 구성 필드는 memo.md를 따른다.
  원본 청크마다 네 벡터를 생성하는 방식이 아니다.
- 7번 입력 구현: `card-views-v1` 템플릿과 관점별 최소 생성 조건은 memo.md의 구현 기본 정책을 따른다.
  최종 Gate pass 카드만 대상으로 생성 가능 텍스트/누락 사유를 구분한다. 내부 경험 ID를 DB 카드 ID로 쓰지 않는다.
  이는 제품 임베딩/저장·검색 계약의 확정이 아니다.
- KURE 호출 구현: 입력 최신성 재확인, 스냅샷 kure-v3-20261016/기본 2,048차원/최대 8개 배치,
  input_type=document를 사용한다. embeddings에는 모델·차원·템플릿·관점별 입력 해시를 보존한다.
  차원은 실행 설정이며 DB/검색 담당과 맞추기 전 저장 스키마를 추가하지 않는다.
- 팀 확인 필요: 검색/DB에서 채택할 모델·차원·버전, 대표 문서에 대한 템플릿과 긴 입력 정책의 적합성,
  별도 테이블 또는 컬럼, 카드 ID와의 연결·유일 제약·인덱스, 영속 템플릿/모델 버전 관리.
- 검색 담당과 확인: 질문별 관점 선택, 복수 검색 결과 통합/리랭킹, 검색 대상 카드 상태,
  모델/차원/템플릿 버전이 다른 벡터의 혼용 방지, 카드 수정 시 재계산 범위.
- 완료 기준: 모델·DB·검색 계약이 일치하고 누락 관점을 가짜/0 벡터로 채우지 않는 통합 테스트.

## C08. 검증 이력 필드와 3순위 경계

- 맞출 담당: 기능 명세/DB 담당, pipeline 담당, 향후 품질 평가 담당.
- 파일: `docs/schema.md`, `knowledge_cards/schemas.py`, `pipeline/nodes/save.py`, `verification/models.py`.
- 미정: quality_score, last_verified_at, verification_version이 MVP Gate 이력인지 사후 품질 평가 이력인지.
  Gate 기준 버전·실행 방식·실패 사유를 기존 필드 또는 별도 기록 중 어디에 저장할지.
- 유지할 경계: 실행하지 않은 품질 점수/시각/평가 버전을 임의 생성하지 않는다.
  점수·가중치·저장된 카드 자동 개선·정기 재평가·변경 이력 기능은 3순위다.
- 완료 기준: 필드 의미가 공용 스키마에 합의되어 있고, 미실행 평가 값이 NULL을 유지하는 테스트.

## C09. 런타임·개발 환경

- 맞출 담당: pipeline, 백엔드 배포 담당.
- 파일: `app/config.py`, 로컬 `.env`(Git 제외), `pyproject.toml`, `uv.lock`, `langgraph.json`, `devtools/README.md`의 공용 설정 예시.
- 확인: OpenAI/Anthropic 키의 모델 접근·예산, Ollama 설치/메모리/컨텍스트 길이, 선택적 VERIFY_MODEL,
  실제 문서 반출·로깅·tracing 설정, 배포 환경의 다운로드/임시 파일 접근·동시 실행 정책.
- 2026-10-30 사용자 확정 설정: OpenAI, Extract/Verify=gpt-6.1-sol, Draft=gpt-6-luna.
  후속 사용자 결정으로 규칙 검증과 LLM 의미 검증을 기본으로 모두 켠다.
  선행 코드 검사를 통과한 초안에서 VERIFY_MODEL을 호출하며 모델 오류/불확실성을 pass로 처리하지 않는다.
  OPENAI_API_KEY/SUPABASE_SERVICE_ROLE_KEY/KURE_API_KEY는 사용자 환경변수에 등록되어 있다.
  2026-10-30 최소 요청으로 Sol·Luna 구조화 응답, Supabase 조회, KURE 임베딩 연결을 확인했다.
  현재 KURE 클라이언트는 읽은 키 앞뒤 공백을 제거한다. 등록 값 자체는 수정하지 않았다.
  새 클라이언트로 네 관점 임베딩을 요청 1회/137토큰으로 확인했다.
  대표 문서 품질·작업당 비용·처리량·할당량 확인과 제품 저장 연결은 아직 미완료다.
  원래 Ollama의 qwen 모델은 자동 설치하지 않았다.
- 개발용 그래프와 예제 응답은 제품 LLM 호출/저장 성공으로 집계하지 않는다.
  개발 서버의 스레드/체크포인트/작업 이력은 로컬 파일이며 저장소에서 제외한다.

## 합의 기록

| 계약 | 현재 상태 | 합의 시 남길 자료 |
|---|---|---|
| C01~C03 입력·제목·진행 | 팀 확인 전 | 함수/요청·응답 예제, 책임자, 결정일, 관련 PR |
| C04~C06 상태·저장·근거 | 팀 확인 전 | 상태 매핑, 실제 저장 함수 타입, 원자성·멱등성, 마이그레이션 |
| C07~C08 임베딩·평가 이력 | 팀 확인 전 | 모델·차원·검색 계약, 필드 의미와 버전 정책 |
| C09 런타임 | 로컬 설정과 실제 KURE 클라이언트 호출 확인. 등록 값 자체는 미수정 | 배포 설정, 대표 문서 품질·비용·할당량, 관측/비밀키 관리 방식 |

합의가 끝나면 해당 구현 파일·공용 문서·테스트를 함께 갱신한다. 이 문서의 예시만 보고 외부 계약을 확정하지 않는다.
