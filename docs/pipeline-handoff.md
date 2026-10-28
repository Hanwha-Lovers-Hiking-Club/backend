<!--
  문서 파이프라인(documents 도메인, LangGraph 기반 지식카드 추출)의 핸드오프 문서.
  프론트 연동 + 디테일 작업에 들어가기 전, 지금까지 만든 모든 파일/노드와
  "왜 이렇게 했는지", "뭘 미뤘는지"를 빠짐없이 남기는 목적.
  작업 순서/완료 체크는 CHECKLIST.md, DB 컬럼 설명은 docs/schema.md가 더 최신일 수 있으니
  둘 다 바뀌면 이 문서도 같이 갱신한다.
-->

# 문서 파이프라인 핸드오프 문서

## 0. 이 문서가 다루는 범위

`app/domains/documents/pipeline/` 전체(7개 노드 + 공통 계약 파일)와, 그 파이프라인이 경계를
넘어가는 지점(`knowledge_cards` 도메인, DB 스키마)까지. **파이프라인 자체는 끝까지 실제로
동작하는 걸 확인했지만, `documents/service.py`(업로드 API)가 이 그래프를 아직 호출하지
않는다** — 7번 섹션에 자세히 적었다. 프론트 연동은 그 연결부터 시작해야 한다.

## 1. 전체 흐름

```
업로드 요청(job) 1개 → IngestionState 1개 → graph.invoke() 1번
parse → chunk → extract → draft_card → verify → embed → save
```

job(업로드 요청) 1개당 그래프를 1번 invoke한다. 문서 1건당 1번이 아닌 이유: 문서 여러 개가
합쳐져 카드 1개가 되거나, 문서 1건에서 카드가 여러 개 나올 수 있어서(M:N), extract/draft_card가
job에 속한 모든 문서의 청크를 한꺼번에 봐야 한다.

각 노드는 `(state) -> 상태 부분 업데이트 dict`로 작성되어 있고(LangGraph 컨벤션), 어느 노드든
실패하면 `state["error"]`를 설정하고 즉시 그 시점에서 멈춘다(fail-fast, 아래 4번 참고). 부분
성공은 없다 — 문서 하나만 실패해도 job 전체가 실패로 끝난다.

## 2. 공통 계약 파일

### `pipeline/state.py` — `IngestionState`

노드끼리 주고받는 유일한 데이터 계약. 필드:

| 필드 | 타입 | 채우는 노드 |
|---|---|---|
| `job_id` | UUID | 호출자(아직 없음, 7번 참고) |
| `documents` | `list[DocumentInput]` | 호출자 |
| `parsed_documents` | `list[ParsedDocument]` | parse |
| `chunks` | `list[Chunk]` | chunk |
| `extracted_elements` | `list[ExtractedElements]` | extract |
| `card_drafts` | `list[CardDraft]` | draft_card |
| `verified_cards` | `list[VerifiedCard]` | verify |
| `embedded_cards` | `list[EmbeddedCard]` | embed |
| `active_step` | int | **어느 노드도 안 씀 — 미구현, 7번 참고** |
| `error` | `str \| None` | 실패한 노드가 설정 |

`Chunk.location`은 "2장 · 답변 근거와 지식 연결" 같은 breadcrumb 문자열. **알려진 한계**:
parse가 docling의 `DoclingDocument`(페이지/좌표 정보 있음)를 마크다운 문자열로 평평하게
만들어버려서, 섹션(헤딩) 단위까지만 가능하고 페이지 번호는 못 뽑는다. 페이지 번호가 필요해지면
parse가 `DoclingDocument`를 버리지 않고 state로 들고 다니게 바꿔야 한다.

처음엔 `embeddings: list[list[float]]`(청크 1:1 대응)이라는 필드가 있었는데, 이건 "청크마다
벡터 1개"라는 옛 가정으로 만든 거라 실제 설계(카드마다 벡터 4개)랑 안 맞아서 삭제하고
`embedded_cards`로 교체했다. `VerifiedCard`에 embeddings를 선택 필드로 끼워넣지 않고 새 상자로
분리한 이유: "`embedded_cards`에 있다 = verify 통과 + 임베딩 완료"라는 보증을 타입으로
표현하기 위함(재작성/폐기 카드와 섞이면 이 보증이 깨짐).

### `pipeline/models.py` — LLM 구조화 출력 모델

- `ExtractedElements` / `ExtractedElementsBatch`(리스트 루트 래퍼, LangChain 구조화 출력 제약 때문)
- `CardExcerptPick`, `CardDraftContent`(draft_card의 LLM 출력. problem~outcome은 LLM에게 다시
  안 시키고 extract 결과를 그대로 복사해서 쓴다)
- `CardEvidenceDraft`, `CardDraft`
- `VerificationResult`, `VerifiedCard`
- `CardEmbeddings`, `EmbeddedCard`

### `pipeline/llm.py` — LLM(OpenAI) 클라이언트

`get_llm()` 하나로 모델/temperature 관리. 지금은 `gpt-4.1-mini`, OpenAI 키로 동작. **해커톤
측에서 다른 키(Claude 등)를 받으면 이 파일만 고치면 된다**(처음엔 Gemini로 가려다 OpenAI 키가
있어서 전환한 적 있음 — 그 흔적).

### `pipeline/embedding.py` — kure-v3 임베딩 클라이언트

`get_embedding_client()` + `embed_documents(texts: list[str])`(배치) + `embed_document(text)`
(단건, 내부적으로 배치 함수를 재사용). `kure.kullm.ai` 실제 페이지를 확인해서 확정한 스펙:

- base_url `https://kure.kullm.ai/api/v1`, OpenAI SDK 그대로 사용 가능
- 모델명 `kure-v3`
- `dimensions`: 256/512/768/1024/2048 중 선택 (지금 1024, 이유는 5번 참고)
- `extra_body={"input_type": ...}`: 질의는 `"query"`, 문서는 `"document"`

**이 파일은 `input_type`을 항상 `"document"`로 고정**했다 — 지식카드를 "저장"할 때 쓰는
용도라서다. 사용자 질문을 임베딩할 때는 `"query"`를 써야 하는데, 그건 챗봇/agent 도메인의 일이라
여기서 안 다룬다(아직 어디에도 구현 안 됨, 7번 참고).

### `pipeline/chunk_formatting.py` — 청크를 XML 태그로 포맷

extract와 draft_card가 공용으로 쓰는 `format_chunks()`. 구분자(`---`) 방식 대신 XML 태그
(`<chunk id="..." location="...">본문</chunk>`)를 쓴 이유: 청크 본문이 마크다운 원문이라
이미 `#`, `---`, ` ``` `를 포함하고 있어서, 단순 구분자는 본문 내용과 충돌할 위험이 있다. 이
포맷은 **state에 저장되지 않는 일회성 문자열**이다 — 그 노드의 LLM 호출 직전에 만들고 쓰고
버린다. chunk 노드 자체는 태그를 전혀 안 붙인다(원문 그대로 유지. draft_card가 나중에 근거
excerpt로 원문 그대로를 써야 해서 이게 중요함).

## 3. 노드별 상세

### 3.1 `nodes/parse.py` — 파일 → 텍스트

- pdf/docx/md/xlsx는 docling으로 변환(`export_to_markdown()`), txt는 docling 없이 그냥 UTF-8
  디코딩(구조가 없는 순수 텍스트라 docling을 거칠 이유가 없음)
- docling 변환은 동기 CPU 작업이라 `asyncio.to_thread`로 이벤트 루프를 막지 않음
- Storage에서 파일을 **재다운로드**해서 처리(업로드 시점에 받은 bytes를 그대로 들고 다니지
  않음) — 백그라운드 워커 분리, 재처리 지원을 위한 선택. 재다운로드 비용은 거의 0에 가깝다고
  판단
- docling의 OCR: 기본 모드(PDF-aware)가 이미 텍스트 레이어가 있는 영역은 OCR을 스킵하는 걸
  확인해서, 별도 분기 코드를 추가하지 않음(조사만 하고 코드 변경 없음)
- 파일 하나라도 파싱 실패하면 job 전체 중단(fail-fast), 어느 파일인지 에러 메시지에 남김

### 3.2 `nodes/chunk.py` — 텍스트 → 청크(+위치)

- **구조 우선 분할**: 먼저 마크다운 헤딩으로 "섹션" 단위로 나누고, 섹션이
  `CHUNK_SIZE_TOKENS`(500, 고정 분할 기준이 아니라 상한선)보다 길 때만 그 안에서
  semchunk+tiktoken으로 추가 쪼갬
- 이 순서가 중요한 이유: 먼저 청크 사이즈로 기계적으로 쪼개면 한 청크가 섹션 경계를 넘어갈 수
  있어서 location이 부정확해짐. 구조 우선으로 가면 청크가 섹션 경계를 절대 안 넘음
- 코드펜스(` ``` `/`~~~`) 안의 `#`는 헤딩으로 안 침(주석 등에 흔함)
- 헤딩 없는 문서(txt 등)는 location이 빈 문자열
- **알려진 한계**(둘 다 받아들이는 수준): (1) semchunk가 섹션 경계의 공백을 트리밍해서,
  청크를 전부 이어붙여도 원문과 byte-exact로 안 맞을 수 있음(공백만, 내용 손실 없음 — 테스트로
  확인). (2) 청크 사이즈 500은 고정값, 실측 기반 조정 전

### 3.3 `nodes/extract.py` — 청크 → 지식카드 구성요소

- **job의 모든 청크를 한 번에** LLM에 보여줌(배치) — 어떤 청크들이 같은 "경험"으로 묶여야
  하는지 판단하려면 전체를 봐야 해서. 임베딩 유사도로 미리 클러스터링하지 않고 **LLM이 직접
  판단**하게 함 (가정(미확정) — 실제로 안 맞으면 별도 그룹핑용 임베딩 도입 재검토)
- 맵-리듀스는 하지 않고 모델 컨텍스트 윈도우 크기(gpt-4.1-mini ~1.05M 토큰)에 의존. 토큰
  안전장치로 `MAX_INPUT_TOKENS = 200_000`(ponytail: 임시값, 실측 기반 조정 예정)을 넘으면 LLM
  호출 전에 즉시 에러
  - **넘치면 job 전체가 실패한다** — extract가 job에 속한 모든 청크를 한꺼번에 봐야 하는
    구조라서 문서 단위 부분 성공이 없음. 자주 터지면 맵-리듀스 재검토
- 프롬프트(`prompts/extract.md`)의 그룹핑 기준은 느슨한 힌트만("같은 기술/프로젝트명이
  언급된다고 자동으로 묶지 말라") — 엄격한 규칙을 강제하지 않음
- **추출 결과가 0건인 것은 정상 성공**(에러 아님). LLM 호출 자체의 실패/malformed 응답만
  에러로 처리. "카드로 만들 게 하나도 없는 문서"는 실제로 있을 수 있는 정상 케이스라서

### 3.4 `nodes/draft_card.py` — 경험 → 카드 초안

- **경험(ExtractedElements)마다 개별 LLM 호출**(배치 아님) — extract와 달리 카드 하나를
  채우는 데 다른 카드 내용을 볼 필요가 없어서, 분리하는 쪽이 결과 품질이 더 좋다고 판단
  (서로 다른 경험의 title/lesson이 한 호출 안에서 섞일 위험을 없앰)
- **지금은 순차 호출**(for 루프, 병렬 아님). 카드 많아져서 느려지면 `asyncio.gather`로
  병렬화 재검토(아직 안 함)
- LLM은 title/summary/context/lesson/technologies/keywords/project_name/department/
  project_date + 청크별 excerpt 후보만 생성. problem/cause/approach/rationale/outcome은
  extract 결과를 그대로 복사(다시 생성 안 함)
- **excerpt 검증/폴백**: LLM이 고른 excerpt가 원본 청크 텍스트의 부분 문자열이면 그대로 쓰고,
  아니면(의역/변형) 그 청크 원문 전체로 폴백 — "원문에서 그대로 가져온 근거"라는 스키마 요구를
  코드로 보장. 트레이드오프: 폴백되면 근거가 청크 전체(최대 500토큰)라 길어짐
- `document_id`/`location`은 LLM이 만들어내지 않고, `source_chunk_ids`로 `state["chunks"]`를
  코드로 조회해서 채움(LLM이 UUID를 지어낼 위험 자체를 차단)
- **알려진 blind spot**: `source_chunk_ids`가 전부 무효(존재하지 않는 chunk_id)면 그 경험을
  조용히 skip하고 로그를 안 남김. extract가 chunk_id를 잘못 짚는 버그가 있어도 지금은 못
  알아챔. 이것 때문에 `extracted_elements`와 `card_drafts`의 리스트 길이가 항상 1:1은 아님
  (→ 4번 graph.py의 재시도 루프 미구현 이유와 직결)
- 구조적 유효성 검사(필수 필드/근거 1개 이상)는 여기서 안 함 — verify로 위임

### 3.5 `nodes/verify.py` — 카드 초안 검증

**2단계 구조**(한 노드 안에 코드 검사 + LLM 검사를 순서대로 둠 — extract의 토큰 안전장치와 같은
"싸고 확실한 것 먼저, 비싼 건 그다음" 패턴):

1. **코드로 구조적 필수 조건 검사**(`_check_structural`): `docs/schema.md`의 DB 제약과
   정확히 같은 기준(title/summary/context/lesson 공백 아님 + 근거 1개 이상 + problem~outcome
   중 최소 1개). 실패하면 **LLM 호출 없이** 바로 `discard`
2. **LLM이 맥락상 타당성 판단**: 구조 검사를 통과한 것만 LLM에게 `CardDraft` 전체 +
   evidences(location/excerpt)를 보여주고 pass/rewrite/discard 판정. 근거가 주장을 실제로
   뒷받침하는지(groundedness)까지 판단하게 함
   - **원본 청크는 다시 조회 안 함** — evidence의 excerpt로 충분하다고 보고, verify가
     `state["chunks"]`에 의존하지 않도록(결합도 낮춤)
   - pass/rewrite/discard 기준도 느슨한 설명+예시로만 줌(엄격한 규칙 강제 안 함)
   - `reason`은 "rewrite일 때 재작성 지침으로 쓸 수 있을 만큼 구체적으로" 쓰라고 지시했지만,
     **이 reason을 실제로 소비하는 코드는 없다**(재시도 루프 자체가 없어서 — 4번 참고)
3. **verify는 판정만 내린다.** `rewrite`일 때 draft_card로 돌아가는 루프는 verify의 일이
   아니라 graph.py가 처리하기로 했었는데, **실제로는 그 루프 자체를 안 만들었다**(4번에서
   자세히)

### 3.6 `nodes/embed.py` — 통과 카드 → 4종 임베딩

- `verified_cards` 중 **`verdict == "pass"`만** 처리
- 카드 필드를 4가지 역할(전체/상황/해결/교훈)로 조합한 텍스트 4개를 만들고, **kure 호출은
  카드당 1번**(`embed_documents`로 4개 텍스트를 배치 전송) — 서로 독립적인 텍스트라 섞일
  위험이 없어서 draft_card와 달리 배치로 가도 손해가 없음
- 조합 텍스트는 "임베딩 설계 가이드" 문서의 역할 명시 포맷(`[상황 및 문제]\n\n환경:\n...`)을
  따름. **빈 필드(None)는 그 섹션을 통째로 생략**("없음"이라고 쓰는 대신) — 의미 없는 텍스트가
  벡터에 섞이는 걸 피하려고
- 차원 1024 선택 이유(5번에서 더 자세히): pgvector 인덱스(HNSW/IVFFlat)가 2,000차원을 넘으면
  못 걸리는데, kure-v3 기본값은 2048이라 바로 못 씀. kure는 차원을 낮춰도 정확도 손실이 크지
  않다는 조사 결과를 바탕으로, 2,000 상한선 대비 여유 있게(저장/연산 비용은 2048 대비 1/4)
  1024로 결정. 카드당 벡터 4개라 이 절감이 4배로 체감됨

### 3.7 `nodes/save.py` — 저장

- documents 도메인이 `knowledge_cards` 테이블을 **직접 만지지 않는다**. 대신
  `knowledge_cards/service.py`에 새로 만든 `create_card(card: dict, evidences: list[dict])`를
  거친다 — pydantic 모델을 그대로 안 넘기고 **plain dict로 변환**해서 넘김(두 도메인이 서로의
  내부 타입을 몰라도 되게)
- `create_card`는 `knowledge_cards` insert → 생성된 id로 `card_documents` insert(근거들)
- **`status="active"`를 명시적으로 넣는다.** `knowledge_cards/service.py`의 `list_cards`가
  `status == "active"`인 카드만 필터링해서 보여주는데, DB 기본값은 `draft`라서, 명시 안 하면
  저장은 되지만 목록에 안 보이는 버그가 됨. MVP 범위에서는 verify를 통과한 카드는 바로
  active(카드 품질 사후관리는 MVP 이후 범위라 CLAUDE.md에 명시됨)
- 카드 하나 저장 실패하면 즉시 fail-fast, **단 이미 저장된 카드는 롤백 안 됨**(카드마다 별도
  insert라 묶어서 되돌릴 수 없음 — 받아들이는 리스크)
- **발견한 것**: pgvector 컬럼은 insert할 땐 파이썬 리스트 그대로 넣으면 되지만, PostgREST로
  **조회**하면 `"[0,0,...]"` 형태의 **문자열**로 돌아온다(리스트 아님). 지금은 쓰기만 하니까
  문제없지만, 나중에 임베딩을 다시 읽어야 할 일(디버깅, RAG 검색 구현 등)이 생기면 파싱이 필요

## 4. `pipeline/graph.py` — 전체 연결

- `langgraph` 패키지를 이번에 처음 추가(`uv add langgraph`) — 그 전까진 이름만 쓰고 실제
  의존성이 없었음
- 노드 순서를 `_NODE_ORDER` 리스트 하나로 정의하고, `_route_after()`가 "에러면 즉시 END, 아니면
  다음 노드"로 분기하는 공용 라우터를 만들어서 노드마다 분기 함수를 따로 안 씀
- `parse`가 비동기 함수라 그래프 호출은 `graph.invoke()`가 아니라 **`await graph.ainvoke()`**
  를 써야 함(동기 invoke 쓰면 에러)

### 중요(명확히 기억할 것): rewrite 재시도 루프는 만들지 않았다

처음 체크리스트를 쓸 때는 "verify 실패 시 draft_card로 돌아가는 조건부 엣지"를 만들기로
했었는데, 실제로는 **만들지 않기로 결정**했다. `rewrite` 판정은 지금 `discard`와 동일하게
"저장 안 함"으로 취급된다(embed/save가 `verdict == "pass"`만 거르므로 자연히 그렇게 됨).

**왜 안 만들었는가**:
1. `card_drafts`/`verified_cards`는 draft_card/verify가 호출될 때마다 리스트를 통째로 새로
   만든다. "통과한 카드는 그대로 두고 rewrite만 다시" 하려면 라운드마다 결과를 누적하는
   구조로 바꿔야 함
2. draft_card가 `source_chunk_ids`를 못 찾으면 경험을 조용히 skip해서(3.4 참고),
   `extracted_elements`와 `card_drafts`의 리스트 길이/순서가 항상 1:1이 아님 — "몇 번째 카드가
   rewrite 났으니 몇 번째 경험만 다시"라는 인덱스 매칭이 안전하지 않음
3. 전체를 다시 돌리면(선택적 재시도 없이) 이미 통과한 카드까지 다시 LLM에 태워서 비용 낭비 +
   오히려 그 카드가 이번엔 나빠질 위험

**업그레이드 경로**(나중에 필요해지면): `CardDraft`에 원본 `ExtractedElements`를 역추적할 수
있는 필드를 추가하고, `card_drafts`/`verified_cards`를 라운드마다 누적하는 reducer로 바꾸고,
graph에 draft_card로 돌아가는 조건부 엣지 + 재시도 횟수 제한(무한루프 방지)을 추가한다. 이
이유는 "카드 품질 사후관리는 MVP 이후 범위"라는 CLAUDE.md 기준에도 부합한다고 보고 결정했다.

### 검증

실제 LangGraph 에러 분기가 의도대로 동작하는지 더미 그래프로 먼저 확인(에러 난 노드 이후
노드는 실행 안 됨). 그 다음 **진짜 샘플 문서로 전체를 끝까지 돌렸다**: 테스트 마크다운을
실제 Supabase Storage에 업로드 → `documents` 행 생성 → `graph.ainvoke()` 실행 → 1개 경험
추출 → 카드 초안 1개 → verify `pass` → 임베딩 포함 카드 1개 → **실제로 DB에
`status=active`로 저장되고 근거 4건(같은 문서의 여러 청크)까지 연결**된 것 확인 → 테스트
데이터(Storage 파일, documents 행, knowledge_cards/card_documents 행) 전부 정리하고 잔여물
없음 재확인.

## 5. DB 스키마 변경

`sql/2026-10-28_add_knowledge_card_embeddings.sql`(Supabase SQL Editor에 직접 실행, 반영
완료) + `sql/tables.sql` + `docs/schema.md`를 같이 수정:

- `create extension if not exists vector;`
- `knowledge_cards`에 `vector(1024)` 컬럼 4개: `content_embedding`, `situation_embedding`,
  `solution_embedding`, `lesson_embedding`
- **차원 1024 결정 배경**: kure-v3 기본값은 2048인데, pgvector는 2,000차원을 넘으면
  HNSW/IVFFlat 인덱스를 못 건다(저장은 되지만 유사도 검색이 매번 전체 테이블 스캔이 됨).
  kure-v3는 차원을 낮춰도 정확도 손실이 크지 않다는 조사 결과를 근거로, 2,000 상한선 대비
  여유 있게(그리고 나머지 선택지인 1536보다 더 싼) 1024로 확정
- **인덱스는 아직 안 걺** — 카드 수/조회 패턴이 쌓이면 추가(지금 해커톤 규모에서는 불필요)

## 6. `knowledge_cards` 도메인과의 경계

- 기존에는 `list_cards`(조회)만 있었음. 이번에 `create_card(card: dict, evidences: list[dict])`
  를 추가해서 저장 경로를 열었다
- `documents/repository.py`에는 원래부터 "지식카드 저장은 이 파일이 아니라 knowledge_cards
  도메인의 service를 호출한다"는 주석이 있었음 — 이번 작업은 그 규칙을 실제로 지킨 것
- `knowledge_cards/schemas.py`의 `KnowledgeCard` 응답 모델에는 임베딩 컬럼이 없음(원래부터
  없었고, 이번에도 안 추가함) — 프론트에 임베딩 벡터를 내려줄 이유가 없어서 의도적으로
  그대로 둠. 프론트 연동 시에도 이 모델에 임베딩을 추가할 필요는 없을 것

## 7. 아직 안 된 것 — 프론트 연동 전에 반드시 확인할 것

**1. `documents/service.py`가 이 그래프를 실제로 호출하지 않는다.** `upload_documents()`는
Storage 업로드 + `documents` 행 생성까지만 하고 `_jobs[job_id]`를 `"status": "queued"`로
둔 채 끝난다. `_jobs`를 `"completed"`/`"failed"`로 바꾸는 코드가 **어디에도 없다** —
`job_events()`(SSE)는 그 상태 전환을 기다리기만 해서, **지금 업로드하면 영원히 "처리 중"으로
보인다.** 프론트 연동의 첫 단계는 이 연결이어야 한다(백그라운드 태스크로 그래프를 invoke하고,
완료/실패 시 `_jobs`를 갱신하는 코드 작성).

**2. `active_step`/`progress`가 그래프의 어떤 노드도 갱신하지 않는다.** `state.py`에 필드는
있지만 7개 노드 중 아무도 이 값을 안 씀. SSE가 진행률을 보여주려면, 1번을 연결할 때 각 노드
실행 후(또는 노드 사이사이) `active_step`을 올려주는 로직이 추가로 필요하다.

**3. verify의 rewrite 재시도 루프 — 4번에서 설명한 대로 의도적으로 미구현.**

**4. 챗봇/RAG 검색 쪽은 전혀 안 건드렸다.** pgvector 유사도 검색용 RPC 함수(`sql/functions.sql`
에 예시가 있지만 실제 `knowledge_cards` 대상 함수는 없음), 사용자 질문 임베딩
(`input_type="query"`) 처리 — 둘 다 `agent`/챗봇 도메인의 일이라 이 핸드오프 범위 밖이고
아직 어디에도 없다.

**5. 카드 품질 사후관리**(`quality_score`, `status`를 `flagged`/`archived`로 바꾸는 로직,
재검증) — CLAUDE.md 기준 MVP 이후 범위로 전혀 손대지 않음.

**6. 환경변수**: `.env`에 `OPENAI_API_KEY`(extract/draft_card/verify용)와
`KURE_API_KEY`(embed용)가 필요하다. 둘 다 `.env.example`에 설명과 함께 적어뒀다.

## 8. 테스트 현황 — 뭘 실제로 확인했고 뭘 안 했는지

| 파일 | 검증 수준 |
|---|---|
| parse, chunk | 로직 테스트(breadcrumb, 코드블록 오탐 방지, 원문 커버리지 등) |
| extract, draft_card, verify (개별) | 로직/안전장치 테스트만(실제 LLM 개별 호출 테스트는 비용 때문에 생략) |
| embedding.py (`embed_document`) | 실제 kure-v3 호출로 1024차원 벡터 확인 |
| embed.py | 로직 테스트 + 실제 kure-v3 호출(텍스트 조합, pass 필터링 포함) |
| save.py / `create_card` | 실제 Supabase insert(더미 문서+카드)로 확인, 테스트 후 정리 |
| graph.py | **실제 전체 파이프라인 1회 end-to-end 실행**으로 parse~save 전부 확인, 테스트 후 정리 |

개별 노드(extract/draft_card/verify)는 "진짜로 LLM을 불러서 결과가 그럴듯한가"를 노드
단위로는 테스트하지 않았고, graph.py의 전체 실행 1회가 그 역할을 겸했다. 더 다양한 입력
(여러 문서, 여러 경험이 섞인 경우, 추출 결과 0건인 경우 등)에 대한 실제 동작은 아직
확인되지 않았다.
