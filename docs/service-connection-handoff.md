<!--
  새 세션용 핸드오프 문서: (A) 파이프라인을 실제 서비스(업로드 API/SSE)에 연결하고, (B) 실제 문서로 파이프라인을 테스트하고,
  (C) 로깅 시스템을 정하는 작업을 이어받기 위한 정보를 모았다.
  이 문서만 읽어도 작업을 시작할 수 있게 썼다. 파이프라인 노드별 변경/미룬 것의 상세는 docs/pipeline-tuning-handoff.md,
  DB 구조는 docs/schema.md, 팀 규칙은 CLAUDE.md와 .claude/skills/backend-conventions/SKILL.md를 본다.
  작업하면서 새로 정한 것/바뀐 것은 이 문서와 pipeline-tuning-handoff.md에 같이 갱신한다.
-->

# 서비스 연결 · 실제 문서 테스트 · 로깅 핸드오프

## 0. 이 문서로 시작하는 법

새 세션에서 이렇게 시작하면 된다: "docs/service-connection-handoff.md 읽고 작업을 이어가자. 먼저 [로깅 / 서비스 연결 / 문서 테스트]부터."
세 작업의 권장 순서는 8번에 있다. 팀장이 에이전트/백엔드 개발 초보라서 **작업 방식(1번)을 꼭 지킨다.**

## 1. 작업 방식 (팀장 선호, 지켜야 함)

- **설명은 초보 눈높이로, "어떻게/왜"를 항상 같이**: 결과만 말하지 말고 무엇을 어떻게 했고 왜 그렇게 했는지, 비유와 쉬운 용어로 설명한다.
- **결정은 선택지로 제시한다**: 선택지마다 장점/단점/트레이드오프를 적고 추천을 첫 번째로 둔다. 구현 중에 생기는 작은 선택도 확인한다.
  팀장이 "추천대로"라고 하면 그대로 진행한다. 데이터가 없으면 "지금 하지 말고 나중에 결정"으로 미루고 기록한다.
- **한 번에 한 단계씩**, 팀장과 같이 진행한다(혼자 앞서 나가지 않는다).
- **냉정하게 판단**: 팀장이 낸 아이디어라도 근거가 약하면 솔직하게 말한다. 모르는 것/확인 못 한 것은 "확인 안 됨"이라고 쓴다.
- **실제 외부 호출 테스트**(LLM, kure, Supabase)는 임시 데이터를 만들고 **끝나면 반드시 정리하고 잔여 0건을 확인**한다.
  이미 이렇게 해왔다(문서, 카드, 청크, Storage 파일을 모두 지움).
- **커밋**: 요청받았을 때만, **주제별로 나눠서**, 메시지는 `feat : 한글 간략 설명` 형식(기존 히스토리와 동일). 푸시/PR은 요청받았을 때만.
  브랜치 규칙(CLAUDE.md): `main`에서 새 브랜치, `main` 직접 push 금지, PR 셀프 머지 가능.
- **문서는 요청받았을 때만** 수정한다(코드 변경에 딸린 이 두 핸드오프 문서 갱신은 예외).
- 코드 스타일: ruff(줄 길이 100, 한글은 2칸으로 계산돼서 주석이 길면 걸린다), 주석은 한글, 기존 스타일에 맞춘다.
  임시 단순화는 `ponytail:` 주석으로 한계와 업그레이드 경로를 남긴다.
- DB 스키마 변경은 `sql/`에 SQL을 적고 `sql/tables.sql`, `docs/schema.md`를 같은 변경에서 고친다. **실제 SQL 실행은 팀장만** 한다.

## 2. 현재 상태

### 브랜치와 커밋
- 브랜치 `app/document/pipeline-tuning`, `main`(`58dffb6`) 위에 **8개 커밋, 푸시/PR 안 함**:
  `7a8b776` state·모델 계약 확장 / `64d44f8` 공통 재시도·파싱 인코딩 / `ea21007` 청크 노드 튜닝 / `abe7723` 기술명 정규화 모듈·카드 검색 /
  `0b84d90` 추출·카드 초안·검증 노드 튜닝 / `0916f41` 카드 임베딩 검증·건너뛰기 / `83a2413` 카드 저장 부분 성공·고아 카드 방지 /
  `41f7b37` 청크 임베딩 인덱싱·그래프 안전망.
- 커밋 안 된 것: `docs/pipeline-tuning-handoff.md`(새 파일), `docs/pipeline-handoff.md` 삭제(스테이지됨), 이 문서.
- 새 작업은 이 브랜치 위에서 이어가거나(미머지 변경에 의존) 먼저 PR을 만들어 머지한 뒤 `main`에서 새 브랜치를 딴다. 어느 쪽으로 할지 팀장과 정한다.

### 파이프라인(완성, 서비스에는 아직 미연결)
```
parse → chunk → extract → draft_card → verify → embed → save → embed_chunks → save_chunks
```
- 코드: `app/domains/documents/pipeline/` (`graph.py`, `state.py`, `models.py`, `nodes/*.py`, `prompts/*.md`, `retry.py`, `stage.py`, `llm.py`, `embedding.py`).
- **job(업로드 요청) 1개당 그래프 1번 실행**: `await build_graph().ainvoke(state)`.
  - 입력 `state`: `{"job_id": UUID, "documents": [{"id", "storage_bucket", "storage_path", "original_filename"}, ...]}` (문서 `id`는 `documents` 테이블의 id).
  - 결과에서 볼 키: `error`(str 또는 없음), `warnings`(list[str] 또는 없음), `saved_card_count`(int), 그 외 중간 산출물(`chunks`, `extracted_elements`, `card_drafts`, `verified_cards`, `embedded_cards`, `embedded_chunks`).
- **실패 정책**: parse/extract는 fail-fast(`error`), draft_card/verify/embed/save는 항목 단위 건너뛰기(`warnings`, 전부 실패하면 `error`),
  embed_chunks/save_chunks는 soft-fail(`warnings`만). 노드 안에서 처리 안 된 예외는 `graph._safe`가 `error`로 바꾼다.
- LLM은 `gpt-4.1-mini`(OpenAI), 임베딩은 kure-v3 1024차원. 환경변수는 `.env`의 `OPENAI_API_KEY`, `KURE_API_KEY`(`.env.example` 참고).
- 튜닝 후 **실제 end-to-end 1회 통과**(문서 2개: md + CP949 txt → 카드 2개, 청크 4개, 에러/경고 없음). 샘플이 작아서 품질 측정은 아님.

### 서비스 쪽(연결 대상)
- `app/domains/documents/router.py`: `POST /api/documents/upload`(202, 파일 여러 개), `GET /api/documents/jobs/{job_id}/events`(SSE).
- `app/domains/documents/service.py`: `upload_documents()`가 검증 → Storage 업로드 → `documents` 행 생성 → `jobs.create_job(job_id, {...status: "queued"...})` 후 반환.
  **그래프를 호출하는 코드가 없고, job 상태를 `completed`/`failed`로 바꾸는 코드도 없다** → 지금 업로드하면 SSE가 영원히 "처리 중".
- `app/domains/documents/jobs.py`: 서버 메모리 dict `_jobs`(`create_job`, `get_job`만 있음, 갱신 함수 없음). 재시작하면 사라지고 워커가 여러 개면 공유 안 됨.
- SSE: `service.job_events()`가 0.5초마다 `_jobs`를 읽어 `progress`(상태가 바뀔 때만) → `completed` 또는 `failed`를 보내고 끝낸다.
  `completed`에는 `job["created_card_count"]`, `failed`에는 `job["message"]`가 **반드시** 있어야 한다(없으면 KeyError).
  이벤트 모양(`schemas.py`, camelCase): `progress{activeStep, progress}`, `completed{jobId, status, createdCardCount}`, `failed{jobId, message}`.
- `STEP_PROGRESS = [16, 32, 48, 66, 84, 100]`(activeStep 0~5, 프론트 `processingSteps`와 명세서 API-003 예시와 같음). 그래프 노드는 9개라 매핑이 필요.
- 기존 테스트: `tests/test_documents_upload.py`, `tests/test_job_events.py`(`jobs._jobs`에 상태를 직접 넣고 `service._wait`를 가짜로 바꿔 검증).

## 3. 작업 A — 서비스 연결

### 이미 정해진 것
- 연결은 실제 문서 테스트 때 한다(팀장 결정).
- parse 하나라도 실패하면 job 실패, 카드 경로의 항목 실패는 건너뛰기, 청크 인덱싱 실패는 경고만(위 실패 정책).
- `completed`의 `createdCardCount`는 **최종 state의 `saved_card_count`**(실제 저장 수. `len(embedded_cards)`가 아님).
- 그래프 호출부는 `error`뿐 아니라 **예외도 잡아서 `failed`로 기록**해야 한다(안 그러면 SSE가 멈춘다).

### 정해야 할 것 (선택지와 추천)
1. **실행 방식**: 업로드 응답은 즉시(202) 주고 그래프는 백그라운드로 돈다.
   - `asyncio.create_task`(추천, 단순): task 참조를 set에 보관하지 않으면 GC로 사라질 수 있다. 서버를 재시작하면 진행 중 job이 사라진다.
   - FastAPI `BackgroundTasks`: 응답 후 실행되지만 이 규모에서 얻는 게 적다.
2. **진행 단계 매핑(노드 9개 ↔ 단계 6개)**: 프론트 `processingSteps` 문구를 팀장에게 확인한 뒤 정한다. 예: parse+chunk → 0, extract → 1, draft_card → 2,
   verify → 3, embed+save → 4, embed_chunks+save_chunks → 5. `NODE_STEP` 같은 매핑을 `graph.py`의 `_NODE_ORDER` 옆에 두고, 서비스가
   `graph.astream(state, stream_mode="updates")`로 노드가 끝날 때마다 `activeStep`/`progress`를 올리는 방식을 권장.
3. **job 전체 타임아웃**: 호출 단위 타임아웃(LLM 120초)은 있지만 job 전체에는 없다. `asyncio.wait_for`로 감싸되, 동기 노드는 스레드에서 돌아
   취소되지 않고 계속 실행될 수 있다(결과만 버려짐)는 점을 감안한다. 값(예: 10분)은 실제 문서 테스트에서 소요 시간을 보고 정한다.
4. **job이 실패했을 때 `documents` 행과 Storage 파일을 남길지 지울지**: 남기면 재처리가 가능하고(parse가 Storage에서 다시 내려받는 구조),
   지우면 쓰레기가 안 쌓인다. 재처리 기능은 아직 없다.
5. **`DocumentInput` 만들기**: `upload_documents()`에는 `_store_files`가 만든 `rows`(storage_path 등)와 `document_ids`가 이미 있다.
   job dict에 `documents`(id, storage_bucket, storage_path, original_filename 목록)를 같이 저장하면 된다(지금은 `document_ids`만 저장).
6. **`warnings`를 프론트에 보여줄지**: 카드 일부 저장 실패, 청크 인덱싱 실패 등이 `warnings`에 담긴다. `completed` 이벤트에 `warnings` 필드를 추가하면
   **프론트 계약 변경**이다(팀장이 프론트도 직접 하므로 같이 정하면 된다). 카드 0개 완료("분석 완료, 카드는 없지만 검색 가능") 안내 문구도 같이.
7. **`jobs.py` 저장소**: 메모리 mock을 유지할지(데모 한정, 재시작 시 소실) 테이블로 옮길지. 옮기면 SQL 변경이 필요(팀장 실행). 데모만이면 유지가 단순.
8. 같은 문서를 다시 업로드하면 같은 카드가 쌓인다(중복 방지 없음, 알려진 한계).

### 구현 스케치
- `jobs.py`에 `update_job(job_id, **fields)` 추가.
- `service.py`에 `_run_pipeline(job_id, documents)`(async): `status="running"` → `astream`으로 단계 갱신 → 끝나면 최종 state에서
  `error`가 있으면 `status="failed"`, `message=error`; 아니면 `status="completed"`, `created_card_count=saved_card_count`(+ `warnings`). 전체를 `try/except Exception`으로
  감싸 예외도 `failed`로 기록. `upload_documents()` 끝에서 `asyncio.create_task(_run_pipeline(...))`(참조 보관).
- 주의: 그래프의 동기 노드(extract, draft_card 등)는 LLM 호출로 오래 블로킹된다. LangGraph가 비동기 실행(`ainvoke/astream`)에서 동기 노드를 스레드 풀로 돌리는지
  **직접 확인할 것**(이벤트 루프가 막히면 SSE가 멈춘다). 확인 방법: 노드 실행 중에 `/health`와 SSE가 응답하는지 본다.
- 테스트: (1) 가짜 그래프로 상태 전이(queued → running → completed/failed), (2) 예외가 `failed`로 기록되는지, (3) 기존 SSE 테스트가 그대로 통과, (4) 실제 서버에서 업로드 → SSE 수신.

## 4. 작업 B — 실제 문서 테스트

### 목적
지금까지 모든 노드의 "나중에" 항목은 **실제 문서를 봐야 결정**되는 것들이다. 이 테스트로 (1) 파이프라인이 실제 문서에서 동작하는지, (2) 품질(환각, 카드 가치)이
어떤지, (3) 임시값들을 어떻게 조정할지 정한다.

### 준비물
실제 산출물과 비슷한 문서 6~10개(설계서, 이슈/장애 기록, 회의록, 보고서 등). 형식을 섞는다: **PDF(표 포함), docx, xlsx(표가 넓은 것), md, txt(CP949 포함), 가능하면 스캔 PDF 1개**.
문서 1~2개는 "경험이 섞여 있는 것"(한 문서에 문제-해결이 여러 개), 1개는 "카드가 안 될 문서"(일정/공지만 있는 것)를 넣으면 좋다.

### 하네스 (스크립트로 직접 돌리기, 서비스 연결 없이도 가능)
업로드 API 대신 Storage 업로드 + `documents` 행 생성 + 그래프 실행 + 결과 확인 + 정리를 스크립트로 한다. 이전 세션에서 쓴 방식(요약):
```python
# 레포 밖(스크래치) 스크립트라면 PYTHONPATH=레포 루트 가 필요하다
from app.config import settings
from app.db import supabase
from app.domains.documents import repository
from app.domains.documents.pipeline.graph import build_graph

# 1) 파일마다 Storage 업로드: repository.upload_file(path, bytes, content_type)  (path = f"{job_id}/{uuid4()}{확장자}")
# 2) repository.insert_documents(rows) -> document_ids
#    rows = {"title","original_filename","size_bytes","storage_bucket","storage_path"}
# 3) final = asyncio.run(build_graph().ainvoke({"job_id": job_id, "documents": [{"id", "storage_bucket", "storage_path", "original_filename"}...]}))
# 4) 결과 출력: final.get("error"), final.get("warnings"), final["saved_card_count"], final["chunks"], 카드/근거/청크 DB 조회
# 5) finally에서 정리: card_documents로 이 문서들의 card_id를 찾아 knowledge_cards 삭제(근거는 cascade),
#    documents 삭제(청크는 cascade), repository.remove_files(paths). 마지막에 잔여 0건 확인
```
실제 문서로 오래 보관하며 볼 때는 정리를 건너뛰는 옵션(`--keep`)을 둔다. 이 하네스를 `scripts/`의 스크립트로 정식화하는 것을 권장
(이전 것은 스크래치라 사라졌다). 돌릴 때 `logging.basicConfig(level=INFO)`로 노드 로그를 같이 본다(작업 C 전까지는 스크립트에서 직접 설정).
비용 주의: 문서당 LLM 호출이 경험 수 × 2(draft_card, verify) + 1(extract)이다.

### 관찰할 것 (로그 키워드와 의미)
| 로그 | 의미 / 판단 |
|---|---|
| `extract: 청크 N개 -> 경험 M개 (LLM 응답 K개)` | M이 0이거나 K와 많이 다르면(무효 chunk id 제거) 추출 문제. 경험 수가 문서 내용에 비해 적정한지 사람이 본다 |
| `extract: 무효/중복 chunk id 제거`, `근거 chunk id가 없어 경험 폐기` | LLM이 id를 틀림. 자주 나오면 짧은 id 매핑(`c1`,`c2`) 도입 검토 |
| `draft_card: ... (excerpt 폴백 N건, 근거 전체 폴백 카드 N개)` | excerpt 폴백이 많으면 공백 무시 비교로 완화 검토. 근거 전체 폴백이 많으면 프롬프트/모델 문제 |
| `LLM 응답 형식 실패, 재시도` (retry 경고) | 형식 오류 재시도가 실제로 필요한지 빈도 확인. 거의 없으면 불필요했던 것 |
| `verify: pass N / rewrite N / discard N` + `rewrite/discard '제목' - 사유` | 너무 관대(대부분 pass)하거나 엄격한지, 사유가 타당한지, excerpt만 봐서 멀쩡한 카드가 버려지는지 |
| `... 건너뜀`, `warnings` 내용 | 항목 실패가 얼마나 나는지 |
| `technology: 사전에 없는 기술` | 사전에 추가할 후보(자주 나오는 것부터) |
| `save: 카드 N개 저장, M개 실패`, `embed_chunks/save_chunks` | 저장/인덱싱 결과 |

### 사람이 확인할 품질 (환각 평가)
- 문서 2~3개를 골라 **카드의 각 필드(problem/cause/approach/outcome/lesson/summary/context)가 원문에 근거하는지**를 직접 대조한다(지어낸 수치/날짜/기술명, 원문에 없는 원인).
  자동 평가 수단이 아직 없어서 이게 유일한 품질 기준이다. 결과를 표로 남기면 프롬프트 수정 전후 비교에 쓸 수 있다.
- 카드로 만들 가치가 있는 경험인데 누락됐는지(재현율), 쓰레기 카드가 있는지(정밀도).
- `lesson`이 공허한 일반론이 아니라 상황+행동이 드러나는지.
- 한 경험이 여러 문서에 걸친 경우 제대로 묶이는지(그룹핑 가정이 맞는지).

### 이 테스트의 결과로 결정할 "나중에" 항목
| 항목 | 판단 근거 |
|---|---|
| parse 변환 품질(PDF 표/레이아웃, xlsx, 스캔 PDF 한국어 OCR, 머리글/바닥글 노이즈), 병렬 처리, 타임아웃 | 변환된 마크다운을 눈으로 확인 |
| 청크 크기 500토큰, 병합 기준 50토큰, 토크나이저(`cl100k` vs `o200k`), PDF 헤딩 평탄화, 표 분할 | 청크 개수/토큰 길이 분포, 표가 잘리는 모양 |
| extract: few-shot, 필드 경계(approach/rationale), "경험" 기준, `MAX_INPUT_TOKENS`, **맵-리듀스 도입**, 중복 경험 | 환각 평가 결과, 입력 크기, 그룹핑 품질 |
| draft_card: 공백 무시 excerpt 비교, `project_name`/`department` 출처(파일명을 맥락으로 줄지), `technologies` 품질, `keywords` 표기 통일 | 위 로그의 폴백 비율, 생성된 값 |
| verify: 판정 기준/few-shot, excerpt만 보는 한계, rewrite 처리(재시도 루프를 만들지, rewrite 구분을 없앨지), 다른 모델로 검증 | 판정 분포와 사유 |
| embed: 역할 라벨 프리픽스 영향, kure 최대 입력 길이, 카드 간 배치 호출, `BATCH_SIZE=32` | 카드 수, 소요 시간, kure 응답 |
| 기술명 사전: 한글 별칭 검토, 사전 확장, 상하위 관계(Next.js ⊂ React) | `사전에 없는 기술` 로그, 실제 표기 |
| 형식 오류 재시도, LLM 타임아웃 120초, job 타임아웃 값 | 재시도 로그 빈도, 실제 소요 시간 |
| 청크 검색 켤지(카드만 vs 카드+청크) | **챗봇이 완성된 뒤** 질문 20~30개로 A/B (챗봇은 다른 팀원 담당) |

각 항목의 배경과 현재 코드는 `docs/pipeline-tuning-handoff.md`의 해당 노드 절("나중에")에 있다. 결정이 나면 그 문서를 같이 갱신한다.

## 5. 작업 C — 로깅

### 현황
- **로깅 설정이 레포 어디에도 없다**(`app/main.py`, `app/config.py` 확인). 파이썬 기본 로거는 WARNING부터만 stderr로 나오므로, uvicorn 기본 실행에서는
  **우리가 남긴 INFO 로그가 안 보일 가능성이 크다**(WARNING은 포맷 없이 나옴). 서버에서 실제로 보이는지 먼저 확인할 것.
- 반대로 서드파티 로그는 시끄럽다: 이전 end-to-end에서 `docling`(INFO 다수), `httpx`/`openai`의 요청 로그가 쏟아졌다 → 기본 수준을 올려야 한다.
- 우리 로그는 모두 `logging.getLogger(__name__)`를 쓴다(로거 이름 = 모듈 경로, `app.domains.documents.pipeline.nodes.verify` 등). `chat/service.py`도 같은 방식.

### 현재 남기는 로그 (위 4번 표에 없는 것 포함)
| 로거 | 수준 | 내용 |
|---|---|---|
| `retry` | WARNING | `<이름> 실패, 재시도 n/m (x초 뒤): <에러>` |
| `nodes/extract` | INFO / WARNING | 청크→경험 개수 / 무효 id 제거, 경험 폐기 |
| `nodes/draft_card` | INFO / WARNING | 경험→카드 개수와 폴백 건수 / 경험 건너뜀 |
| `nodes/verify` | INFO / WARNING | 판정 분포 / rewrite·discard 사유, 검증 실패로 건너뜀 |
| `nodes/embed` | INFO / WARNING | 임베딩한 카드 수 / 카드 건너뜀 |
| `nodes/save` | INFO / WARNING | 저장·실패 수 / 카드 저장 실패 |
| `nodes/embed_chunks`, `save_chunks` | INFO / WARNING / ERROR(exception) | 청크 수 / 실패·정리 |
| `graph` | ERROR(exception) | 노드의 예기치 못한 예외(스택트레이스 포함) |
| `core/technology/normalizer` | INFO | 사전에 없는 기술 |
| `knowledge_cards/service` | ERROR(exception) | 근거 저장 실패 후 카드 정리도 실패 |

### 논의하고 정할 것 (선택지, 제안 기본값)
1. **누가 어디서 보나**: 데모/개발은 콘솔(stdout)이면 충분한지, 파일 로테이션이나 외부 수집이 필요한지. 제안: 콘솔만(Phase 1).
2. **형식**: 사람이 읽는 텍스트(제안) vs JSON(나중에 검색/집계가 쉬움). 팀 규모와 기간을 보면 텍스트가 단순.
3. **수준**: 앱 로거(`app.*`)는 INFO, `docling`/`httpx`/`openai`/`httpcore`/`hpack` 등은 WARNING. 환경변수 `LOG_LEVEL`로 조절하게 할지
   (`app/config.py`에 추가하면 `.env.example`도 같이 고친다는 규칙이 있다).
4. **`job_id`로 구분**: 동시에 여러 job이 돌면 로그가 섞인다. `contextvars`에 `job_id`를 넣고 로깅 필터로 모든 줄에 붙이는 방식을 제안.
   `asyncio.to_thread`는 컨텍스트를 복사하지만 **LangGraph가 동기 노드를 실행하는 스레드에서도 유지되는지는 확인 필요**.
5. **민감 정보**: 로그에 문서 내용이 새지 않게 한다. 현재 남는 것: 카드 제목, 경험의 problem 앞 40자(`_label`), `rewrite/discard` 사유(LLM이 쓴 글), 에러 메시지.
   특히 **pydantic `ValidationError`는 메시지에 LLM이 낸 입력 값을 그대로 포함**하고, `logger.exception`은 스택트레이스를 남긴다 → 에러 메시지는 길이를 자르는(예: 200자) 방안을 검토.
6. **어디에 설정할지**: `app/core/logging.py`(설정 함수) + `app/main.py`에서 한 번 호출을 제안. uvicorn `--reload`와 `uvicorn` 자체 로거 포맷도 같이 고려.

### 완료 기준
- 서버를 실행해서 업로드 → 처리 중에 위 INFO/WARNING 로그가 콘솔에 보인다(서드파티 소음 없음).
- 로그 줄에 `job_id`가 붙어서 두 job을 동시에 돌려도 구분된다(선택).
- 로그를 보고 4번의 "관찰할 것" 표를 읽을 수 있다.

## 6. 알려진 이슈

- **테스트가 `.env` 없이는 실패한다(확인함)**: `app/config.py`의 `Settings`가 `OPENAI_API_KEY`, `KURE_API_KEY`를 필수로 요구하는데 `tests/conftest.py`는
  Supabase 변수만 더미로 넣는다. `.env`가 없는 환경(새 팀원, CI)에서는 import 단계에서 실패한다. conftest에 더미 값을 추가하면 된다.
- **낡은 테스트**: `tests/test_documents_upload.py::test_upload_rejects_unsupported_type`는 `sheet.xlsx`를 "미지원"으로 기대하지만 xlsx는 지원 형식이다.
  미지원 확장자를 `.exe` 등으로 바꾸면 된다(코드는 맞음).
- **줄 길이 경고**(ruff E501, 기존 것): `parse.py`의 `# 문서 하나라도 실패하면 …` 주석, `documents/service.py` 2행.
- `jobs._jobs`는 메모리 mock(재시작 시 소실, 워커가 여러 개면 공유 안 됨).
- 한글 기술명 별칭 사전은 초안이라 팀 검토가 필요하다(`app/core/technology/aliases.py`).
- 기술명 상하위 관계(Next.js ⊂ React)는 반영 안 됨.
- 카드 중복 방지 없음(같은 문서 재업로드 시 같은 카드가 쌓임).

## 7. 알아두면 좋은 것

### 핵심 파일 맵
| 경로 | 역할 |
|---|---|
| `app/domains/documents/service.py`, `jobs.py`, `router.py`, `schemas.py` | 업로드 API, job 상태, SSE (연결 작업 대상) |
| `app/domains/documents/pipeline/graph.py` | 노드 순서, `_safe`(예외 안전망), 실패 정책 주석 |
| `.../pipeline/state.py`, `models.py` | 노드 간 데이터 계약 |
| `.../pipeline/nodes/*.py`, `prompts/*.md` | 9개 노드와 프롬프트 |
| `.../pipeline/retry.py`, `stage.py`, `llm.py`, `embedding.py` | 공통 재시도, 항목 단위 마무리, LLM/kure 클라이언트 |
| `app/core/technology/` | 기술명 정규화(사전, 변환 함수) |
| `app/domains/knowledge_cards/service.py` | 카드 저장(`create_card`), 목록 검색, 응답 변환 |
| `scripts/normalize_card_technologies.py` | 기존 카드의 기술명 정규화(미리보기 기본, `--apply`) |
| `sql/`, `docs/schema.md` | DB 구조(`document_chunks` 포함, 적용 완료) |

### 명령어
```bash
uv sync                                              # 의존성 설치
uv run pytest tests -q                               # 테스트 (.env 필요, 위 6번 참고)
uv run ruff check app tests scripts && uv run ruff format app tests scripts
uv run uvicorn app.main:app --reload                 # 서버 (http://localhost:8000/health)
```
스크래치 스크립트를 레포 밖에서 돌릴 때는 `PYTHONPATH=<레포 루트>`가 필요하다(스크립트 폴더만 import 경로에 잡힌다).

### 이미 알려진 사실
- OpenAI SDK는 전송 오류(연결 오류, 408/409/429/5xx)를 0.5~8초 백오프로 2회 자동 재시도한다. LLM(`llm.py`)과 kure(`embedding.py`) 모두 해당.
  우리 `retry.py`는 SDK 재시도와 곱해지지 않게 LLM 응답 형식 오류(`ValueError` 계열)에만, 다운로드에만 쓴다. **DB insert에는 재시도를 쓰지 않는다**(중복 위험).
- 챗봇(RAG 검색, 질문 임베딩, 응답 스키마 확장)은 **다른 팀원이 만들고 있다.** 그 팀원에게 전달할 내용은 `docs/pipeline-tuning-handoff.md` 6번에 정리했다.
- 챗봇이 아직 mock이라 "카드만 vs 카드+청크" A/B는 챗봇 완성 후에 할 수 있다.

## 8. 권장 진행 순서

1. **로깅 먼저**(작음): 이후 작업에서 로그를 보며 진행하려면 설정이 먼저 있어야 한다. 논의(5번) → 설정 → 서버에서 보이는지 확인.
2. **서비스 연결**(3번): 결정 8개를 팀장과 정하고 구현. 연결하면 프론트와 실제 업로드로 end-to-end가 된다.
3. **실제 문서 테스트**(4번): 하네스(정식 스크립트화 권장) + 서비스 경로 둘 다로 돌려본다. 결과를 표로 남기고 "나중에" 항목을 결정한다.
4. 결정/변경은 `docs/pipeline-tuning-handoff.md`에 반영하고, 주제별로 커밋한다(요청 시).

세 작업이 서로 독립적이라 순서를 바꿔도 된다. 단, 3번(테스트)은 로그(1번)가 있어야 효율적이다.
