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

- [ ] 추출 요소로 카드 초안 생성 + 근거(location/excerpt) 연결 + "교훈" 필드 생성
  - 완료 기준: 초안의 각 필드가 어떤 청크/위치에서 왔는지 역추적된다.
  - 역할 범위: extract가 뽑은 `ExtractedElements`(그룹핑 + problem/cause/approach/rationale/outcome)를
    받아서 title/summary/context/lesson/technologies/keywords/project_name/department/project_date를
    채우고 근거를 붙여 `CardDraft`로 완성하는 게 이 노드의 일. 구조적 유효성 검사는 하지 않음(verify로 위임).

## 5. verify — 검증

- [ ] (코드) 구조적 필수 조건 검사 — LLM 호출 전에 먼저
  - 완료 기준: `docs/schema.md`의 "구조적 필수 조건" 그대로: 1) title/summary/context/lesson 전부
    공백 아닌 값 있어야 함, 근거 1개 이상 있어야 함 2) problem/cause/approach/rationale/outcome 중
    최소 1개는 공백 아닌 값 있어야 함. 결정론적 코드 검사라 LLM 관여 없음 — 실패하면 LLM 호출 없이
    바로 `VerificationResult(verdict="discard", reason="구조적 필수 조건 미충족: ...")`로 끝남.
- [ ] (LLM) 구조 통과한 초안만 맥락상 검증 — 통과 / 재작성 요청 / 폐기로 판정 (`models.VerificationResult` 사용)
  - 완료 기준: 의도적으로 부실한 초안을 넣으면 재작성 요청 또는 폐기로 판정된다.
  - 가정(확정): 구조 검사(코드)와 맥락 검사(LLM)를 같은 verify 노드 안에서 순서대로 실행한다
    (구조 검사가 싸고 확실해서 비싼 LLM 호출 전에 먼저 거른다 — extract의 토큰 안전장치와 같은 패턴).

## 6. embed — 지식카드 임베딩 생성 (통과한 카드만)

- [ ] kure-v3 API로 카드 필드를 조합해 4종 임베딩 생성: `content_embedding`(전체),
  `situation_embedding`(상황·문제), `solution_embedding`(해결), `lesson_embedding`(교훈)
  - 완료 기준: 카드 1개 입력 시 4개 벡터가 전부 나온다. (조합 텍스트는 "임베딩 설계 가이드" 문서의
    역할 명시 포맷을 따른다, 예: `[상황 및 문제]\n\n환경:\n...\n\n발생한 문제:\n...`)
- [ ] DB 스키마: `knowledge_cards`에 임베딩 컬럼 4개 추가 (`docs/schema.md` + `sql/tables.sql` 같이 수정)
  - 완료 기준: pgvector 컬럼 4개 생성, 차원은 kure-v3 호출 시 쓴 `dimensions`와 일치.

## 7. save — 저장

- [ ] 검증 통과 + 임베딩까지 끝난 카드를 `knowledge_cards` 도메인 service 경유로 저장 (테이블 직접 접근 금지)
  - 완료 기준: 저장 후 지식카드 페이지에서 바로 조회된다.

## 8. graph — 전체 연결

- [ ] `graph.py`: 노드 연결 + verify 실패 시 draft_card로 돌아가는 조건부 엣지
  - 완료 기준: 샘플 문서 업로드 → 끝까지 실행 → 지식카드 1개 이상 저장까지 한 번에 돈다.
