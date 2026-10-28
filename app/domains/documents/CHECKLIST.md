# 문서 파이프라인 개발 체크리스트

LangGraph 기반 지식카드 추출 파이프라인. 하나씩 순서대로 구현하고, 완료되면 체크한다.
노드 1개 = 파일 1개 원칙은 `pipeline/nodes/__init__.py` 주석 그대로 따른다.

## 0. 공통 계약 (노드보다 먼저 확정)

- [x] `state.py`: `IngestionState` 필드 정의 (노드 간 유일한 데이터 계약)
  - 완료 기준: 7개 노드가 입출력으로 쓸 필드가 전부 들어있고, 필드 하나로 어떤 타입인지 설명 없이 알 수 있다.
- [x] `models.py`: LLM 구조화 출력 모델 정의 (`ExtractedElements`, `CardDraft`, `VerificationResult` 등)
  - 완료 기준: extract/draft_card/verify 노드가 이 모델을 그대로 import해서 쓸 수 있다.
- [x] `llm.py`: LLM/임베딩 클라이언트 생성 함수
  - 완료 기준: 노드에서 `get_llm()` 같은 함수 하나만 호출하면 되고, 모델/temperature는 여기서만 바꾸면 된다.

## 1. parse — 파일에서 텍스트 추출

- [x] pdf/docx/md/txt/xlsx 포맷별 텍스트 추출
  - 완료 기준: 포맷별 샘플 파일 하나씩 넣었을 때 빈 문자열 없이 텍스트가 나온다.

## 2. chunk — 청크 분리 (근거 위치 포함)

- [x] 텍스트를 청크로 분리하면서 위치(location) 정보 유지
  - 완료 기준: 청크 하나를 골라 원본에서 그 위치를 다시 찾을 수 있다 (근거 하이라이트용).

## 3. extract — 지식카드 구성요소 추출

- [x] `models.py`: 구조화 출력 래퍼 모델 추가 (`ExtractedElementsBatch` — `elements: list[ExtractedElements]`)
  - 완료 기준: LangChain structured output이 리스트를 바로 루트로 못 받는 제약 때문에 필요.
    토론 불필요, 바로 추가.
- [x] `extract.py`: job의 모든 청크를 XML 태그로 포맷 (`<chunk id="..." location="...">본문</chunk>`)
  - 완료 기준: 청크 본문에 들어있는 마크다운 구분자(`#`, `---`, ` ``` `)와 겹치지 않는다.
    순수 로직 처리(LLM 관여 없음), `id`는 결과의 `source_chunk_ids`로 그대로 복사될 값.
- [x] `extract.py`: 토큰 안전장치 — 포맷된 입력이 임계값을 넘으면 LLM 호출 전에 `state["error"]`로 즉시 중단
  - 완료 기준: 가정(확정): 맵-리듀스는 지금 안 함(모델 컨텍스트 윈도우 크기에 의존). 넘치면
    job 전체 실패로 전파됨(문서별 부분 성공 없음) — extract가 job에 속한 모든 문서의 청크를
    한꺼번에 봐야 하는 M:N 그룹핑 구조라서 그렇다. 자주 터지면 맵-리듀스 재검토.
    임계값 숫자는 나중에 전체 파이프라인 테스트하면서 정한다.
- [x] `prompts/extract.md`: 추출 지시문 작성
  - 완료 기준: 문제/원인/접근/근거/결과 추출 지시 + "같은 경험" 그룹핑은 느슨한 힌트로만 제시(엄격한
    규칙 강제 안 함) + 추출할 게 없으면 빈 리스트 반환해도 정상(에러 아님)이라고 명시.
- [x] `extract.py`: 위 조립 — LLM 호출 → 성공 시 `state["extracted_elements"]`에 매핑
  - 완료 기준: 샘플 문서 하나로 돌렸을 때 빈 값 없이 구조화된 요소가 나온다. LLM 호출 자체의
    실패/malformed 응답만 에러로 처리하고, 결과가 빈 리스트인 것은 정상 성공으로 처리.
  - 가정(미확정): 문서 여러 개의 청크 중 어떤 것들이 같은 카드로 묶여야 하는지는 **LLM이 청크들을
    직접 읽고 판단**하게 한다(임베딩 유사도로 미리 클러스터링하지 않음). 이게 실제로 안 맞으면
    청크 단계에 별도 그룹핑용 임베딩을 추가하는 걸 재검토한다.

## 4. draft_card — 카드 초안 생성

- [x] 공용 포맷터 분리 (`chunk_formatting.py`): extract의 XML 태그 포맷팅을 재사용
  - 완료 기준: extract.py와 draft_card.py가 같은 함수로 청크를 `<chunk id="..." location="...">`로 포맷한다.
- [x] `models.py`: draft_card LLM 출력용 모델 추가 (`CardExcerptPick`, `CardDraftContent`)
  - 완료 기준: problem/cause/approach/rationale/outcome은 extract 결과를 그대로 복사하므로 LLM에게
    다시 안 시킨다. LLM은 title/summary/context/lesson/technologies/keywords/project_name/
    department/project_date + 청크별 excerpt 후보(`excerpts: list[CardExcerptPick]`)만 생성.
- [x] `draft_card.py`: 경험(ExtractedElements)마다 개별 LLM 호출 (배치 아님)
  - 완료 기준: 카드 1개 = LLM 호출 1번. 각 호출의 입력은 그 경험의 source_chunk_ids로 조회한
    원본 청크들(XML 포맷) + problem/cause/approach/rationale/outcome.
  - 가정(확정): 지금은 순차 호출(for 루프, 병렬 아님). 카드 많아져서 느려지면 그때 asyncio.gather로
    병렬화 재검토.
- [x] `draft_card.py`: excerpt 검증 — LLM이 고른 발췌문이 원본 청크에 그대로 있는지 코드로 확인
  - 완료 기준: LLM이 고른 excerpt가 해당 청크 텍스트의 부분 문자열이면 그대로 쓰고, 아니면(의역/변형)
    그 청크의 원문 텍스트로 폴백한다. "원문에서 그대로 가져온 근거"라는 스키마 요구를 코드로 보장.
- [x] `draft_card.py`: 조립 — LLM 결과 + extract 결과 + 청크 조회로 `CardDraft` 완성
  - 완료 기준: 초안의 각 필드가 어떤 청크/위치에서 왔는지 역추적된다. document_id/location은
    source_chunk_ids로 청크를 조회해 코드로 채움(LLM이 UUID를 만들어내지 않음).
  - 역할 범위: 구조적 유효성 검사(필수 필드/근거 1개 이상)는 하지 않음 — verify로 위임.

## 5. verify — 검증

- 역할 범위(확정): verify 노드는 판정(pass/rewrite/discard)만 내린다. `rewrite`일 때 draft_card로
  돌아가는 재시도 루프는 verify의 일이 아니라 8단계 `graph.py`의 조건부 엣지가 처리한다.
- [x] (코드) 구조적 필수 조건 검사 — LLM 호출 전에 먼저
  - 완료 기준: `docs/schema.md`의 "구조적 필수 조건" 그대로: 1) title/summary/context/lesson 전부
    공백 아닌 값 있어야 함, 근거 1개 이상 있어야 함 2) problem/cause/approach/rationale/outcome 중
    최소 1개는 공백 아닌 값 있어야 함. 결정론적 코드 검사라 LLM 관여 없음 — 실패하면 LLM 호출 없이
    바로 `VerificationResult(verdict="discard", reason="구조적 필수 조건 미충족: ...")`로 끝남.
- [x] (LLM) 구조 통과한 초안만 맥락상 검증 — 통과 / 재작성 요청 / 폐기로 판정 (`models.VerificationResult` 사용)
  - 완료 기준: 의도적으로 부실한 초안을 넣으면 재작성 요청 또는 폐기로 판정된다.
  - 가정(확정): 구조 검사(코드)와 맥락 검사(LLM)를 같은 verify 노드 안에서 순서대로 실행한다
    (구조 검사가 싸고 확실해서 비싼 LLM 호출 전에 먼저 거른다 — extract의 토큰 안전장치와 같은 패턴).
  - 가정(확정): LLM에게 CardDraft 필드 전체 + evidences(location/excerpt)를 보여줘서, 근거가
    주장을 실제로 뒷받침하는지까지 판단하게 한다. 원본 청크 전체를 다시 조회하지는 않음(evidence의
    excerpt로 충분하다고 판단, verify가 state["chunks"]에 의존하지 않도록).

## 6. embed — 지식카드 임베딩 생성 (통과한 카드만)

- 가정(확정): 임베딩 차원은 **1024**. pgvector 인덱스(HNSW/IVFFlat)는 2,000차원까지만 걸 수 있어서
  kure-v3 기본값(2048)은 못 쓰고, kure-v3는 차원을 낮춰도 정확도 손실이 크지 않다는 조사 결과를
  바탕으로 2,000 상한선 대비 여유 있게 1024로 정함 (저장/연산 비용은 2048 대비 1/4, 2개 분야
  요소가 섞인 복합 텍스트(`content_embedding` 등)의 표현력도 과하게 희생하지 않는 값).
- [x] DB 마이그레이션: pgvector 익스텐션 + `knowledge_cards`에 `vector(1024)` 컬럼 4개 추가
  (`content_embedding`, `situation_embedding`, `solution_embedding`, `lesson_embedding`)
  - 완료 기준: `sql/tables.sql` + 새 날짜 마이그레이션 파일 + `docs/schema.md` 같은 PR에서 같이 수정.
    (Supabase SQL Editor에 사용자가 직접 붙여넣어 실행 — 우리 코드가 자동 실행하지 않음)
  - **TODO(사용자)**: `sql/2026-10-28_add_knowledge_card_embeddings.sql`을 Supabase SQL Editor에서
    직접 실행해야 실제 DB에 반영됨. 코드는 아직 실행 안 한 상태.
- [x] `state.py` 재정비: 지금 있는 `embeddings: list[list[float]]`(청크 단위, stale)를 제거하고
  카드 단위 4종 벡터를 담을 새 필드/타입으로 교체
  - 완료 기준: embed 노드의 입력(verified_cards 중 pass만)과 출력(4종 벡터 붙은 카드) 모양이
    타입으로 명확히 드러난다.
  - 가정(확정): `card_drafts`/`verified_cards`처럼 새 상자(`CardEmbeddings`, `EmbeddedCard`,
    `state.embedded_cards`)로 분리했다 — `VerifiedCard`에 embeddings를 선택 필드로 끼워넣지 않음.
    "embedded_cards에 있다 = verify 통과 + 임베딩 완료"라는 보증을 타입으로 표현하기 위함.
- [ ] kure-v3 클라이언트 함수 (`llm.py`처럼 한 곳에 모음)
  - 완료 기준: `KURE_API_KEY`를 `.env`/`config.py`에 추가. 아직 키 발급 전이라 실제 호출 테스트는
    키 받은 뒤로 미룸.
- [ ] `embed.py`: pass 판정 카드마다 카드 필드를 4가지 역할별로 조합해 kure-v3로 임베딩 생성
  - 완료 기준: 조합 텍스트는 "임베딩 설계 가이드" 문서의 역할 명시 포맷을 따른다
    (예: `[상황 및 문제]\n\n환경:\n...\n\n발생한 문제:\n...`). 카드 1개 입력 시 4개 벡터가 전부 나온다.

## 7. save — 저장

- [ ] 검증 통과 + 임베딩까지 끝난 카드를 `knowledge_cards` 도메인 service 경유로 저장 (테이블 직접 접근 금지)
  - 완료 기준: 저장 후 지식카드 페이지에서 바로 조회된다.

## 8. graph — 전체 연결

- [ ] `graph.py`: 노드 연결 + verify 실패 시 draft_card로 돌아가는 조건부 엣지
  - 완료 기준: 샘플 문서 업로드 → 끝까지 실행 → 지식카드 1개 이상 저장까지 한 번에 돈다.
