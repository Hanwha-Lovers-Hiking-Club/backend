# 문서 파이프라인 설계 메모

이 문서는 문서 파이프라인의 현재 상태, 확정된 설계와 미정 계약을 정리한다.
개발 진행 항목은 [checklist.md](checklist.md)에서 관리한다.
내용의 적용 범위는 pipeline 하위다.
외부 파일 경로는 연결 상대를 설명하며, 수정 권한을 넓히지 않는다.

## 확인된 상태 — 2026-10-30 로컬 기준

- `models.py`에 3번 작업의 `EvidenceRef`, `ExtractedField`, `ExtractedElements`를 구현했다.
  `ExtractionResult`로 0~N개 독립 경험을 표현한다.
- 4번의 형식별 파서, DocumentIR, 구조 기반 청킹, Parse/Normalize/Chunk 노드와 최소 State를 구현했다.
- 5번 Extract/Draft 노드와 작성 모델, 근거 연결, 생략 사유, OpenAI/Ollama/Anthropic 비동기 어댑터를 구현했다.
  6번 Verification Gate와 실제 LangGraph 조건부 재처리를 연결했다.
  7번의 결정적 입력 텍스트 생성과 실제 KURE Embed를 연결했다. Save와 제품 업로드·SSE는 아직 미구현이다.
- `fake_function.py`에 개발용 `fake_save_cards`가 있다. DB에 저장하지 않는 독립적인 가짜 함수다.
  그래프·save 노드에는 아직 연결하지 않았다. 실제 서비스의 확정 계약이 아니다.
- `knowledge_cards/service.py`에는 조회용 `list_cards`가 있으나 카드 생성/저장 함수는 없다.
- 원본 파일은 Supabase Storage, 문서 메타는 `documents`, 카드는 `knowledge_cards`,
  카드와 원본 근거 연결은 `card_documents`에 두는 구조가 스키마에 명시되어 있다.
- 위 내용은 로컬 코드/SQL/문서 확인 결과다. 실제 원격 DB와 버킷 설정을 검증했다는 뜻은 아니다.
- DB 필드가 정해져 있는 것과 함수가 받을 입력·반환값이 정해져 있는 것은 다르다.
- `pyproject.toml`에 pdfplumber, python-docx, openpyxl, markdown-it-py를 추가했다.
  reportlab은 최소 PDF 테스트 문서를 만들기 위한 개발 의존성이다. 데이터셋 생성 기능은 없다.
  OpenAI/Anthropic SDK와 Ollama용 HTTP 클라이언트를 사용한다. LangGraph는 직접 의존성이며 CLI[inmem]는 개발 의존성이다.
  2026-10-30 사용자 승인으로 OpenAI를 기본 공급자로 변경했다. Extract/Verify는 gpt-6.1-sol,
  Draft는 gpt-6-luna다. 후속 사용자 결정으로 LLM Verify를 기본 활성화했으며 규칙 검증도 필수다.
  공급자 변경 시 모델 ID도 함께 변경한다.

## 3번 구현 — 내부 추출 스키마

`models.py`의 모델은 내부 추출 계약이며 기존 KnowledgeCard API/DB 모델을 대체하지 않는다.
외부 API 스키마, DB 컬럼, SQL은 변경하지 않았다.

| 모델 | 역할 |
|---|---|
| `EvidenceRef` | 문서 UUID, 블록 ID, 원본 위치, 인용문, 선택적인 문자 구간 |
| `ExtractedField` | 문자열/None인 `value`, 사실 상태 `status`, 근거 ID 목록 `evidence_refs` |
| `ExtractedElements` | 독립 경험 하나의 context/problem/cause/approach/rationale/outcome/lesson/technologies/keywords |
| `ExtractionResult` | `experiences: list[ExtractedElements]`. 경험별로 근거와 상태를 독립 보존 |

본문 7개는 ExtractedField이고 technologies/keywords는 메타데이터로 감싸지 않은 list[str]이다.
`evidence_index: dict[str, EvidenceRef]`가 근거를 소유하고 필드는 해당 맵의 ID를 참조한다.
공유 근거를 여러 필드가 참조할 수 있다. 존재하지 않거나 공백인 근거 ID는 스키마 단계에서 거부한다.
근거 인덱스와 좌표는 파서/정규화 코드가 제공하고, 후속 LLM은 제공받은 근거 ID를 선택하도록 연결한다.
초기 태그 객체형 내부 모델은 영속화하지 않았다. 현재 문자열 배열 이외의 형태를 조용히 변환/폐기하지 않고 거부한다.
기존 공개 API/DB의 문자열 배열은 그대로 유지한다.
title/summary 작성과 기존 필드 이름을 유지하는 초안 변환은 아래 5번에서 구현했다.

### 사실 상태와 기본 검증

- `confirmed`: 원문에 명시된 사실로 추출한 값.
- `hypothesis`: 원문 자체가 가설로 제시한 값.
- `inferred`: 근거에서 도출한 추론.
- `unknown`: 정보 부재. value는 None이며 누락한 필드의 기본 상태다.
- `conflicted`: 근거가 충돌한 상태. 결론을 택하지 않고 value=None일 수 있다.
- unknown 이외의 상태는 근거를 요구한다. unknown은 미확인 사실을 설명하는 근거를 가질 수 있다.
- confirmed/hypothesis/inferred는 값도 요구한다. technologies/keywords는 정보가 없으면 빈 배열이다.
- 사실 상태는 카드의 draft/active/flagged/archived와 별개이며 자동 매핑하지 않는다.
- 공백뿐인 텍스트, 알 수 없는 필드, 값/상태/근거의 불일치 등은 ValidationError로 거부한다.

### 근거 구간 계약 — 4번/6번에서 이어받을 내용

- document_id는 documents.id와 같은 UUID이며 block_id와 location은 형식에 독립적인 문자열이다.
- start_char/end_char는 원문을 보존한 블록 text의 Python 문자 인덱스다.
  0부터 시작하고 끝은 제외한다. excerpt는 text[start_char:end_char]에 해당하는 연속 구간이다.
- 둘 다 생략할 수 있다. 제공 시 둘 다 있어야 하며 LLM에게 좌표 계산을 맡기지 않는다.
- excerpt는 공백·줄바꿈을 포함해 보존한다. 모델이 임의로 strip/정규화하지 않는다.
- 이번 모델은 구간의 순서·길이를 검사한다. 블록 존재, 실제 위치 범위와 인용문 일치는 6번에서 검사한다.
- 여러 문서의 근거를 한 필드에 보존할 수 있다. 특정 파일 형식이나 GitHub 컬럼명을 가정하지 않는다.
- 정규화가 전체 블록의 문자 구간과 evidence_id를 계산한다. ID는 문서·블록 내용에 기반해 재현 가능하다.

모델의 검증 통과는 주장의 사실성을 입증하지 않는다. 의미 검증의 실제 범위는 아래 6번을 따른다.
필드별 근거의 영속 저장은 8번에서 결정하며, 현재 card_documents 구조를 임의 변경하지 않았다.

## 4번 구현 — Parse / Normalize / Chunk

| 모듈 | 책임 |
|---|---|
| `document.py` | DocumentSource, ParsedBlock/ParsedDocument, DocumentBlock/DocumentIR, Chunk, 처리 옵션 |
| `parsers/` | 형식별 로더와 ParseError. 원본 파일 수정·DB/Storage 접근 없음 |
| `normalization.py` | 원본 블록 텍스트를 다시 쓰지 않고 순서·블록 ID·근거 ID·인덱스 부여 |
| `chunking.py` | 섹션과 블록 단위 그룹화. 전략 교체가 가능한 독립 함수 |
| `nodes/parse.py` | 동기 파일 파싱을 별도 스레드로 실행 |
| `nodes/normalize.py`, `nodes/chunk.py` | State 입력으로 위 함수를 호출하고 부분 업데이트 반환 |

### 최소 식별자 계약

- document_id: 업로드 담당이 제공하는 documents.id UUID. 파이프라인이 별도 DB 행을 만들지 않는다.
- block_id: 정규화가 블록 순서와 내용/위치 메타데이터로 생성하는 UUID5 문자열.
- location: 표시 가능한 원본 위치 문자열. 자세한 페이지/bbox/시트/셀/줄 좌표는 블록 metadata에 보존한다.
- evidence_id: 해당 문서의 전체 블록 근거를 가리키는 재현 가능한 UUID5 문자열.
  DocumentBlock.evidence_id 및 evidence_index의 키가 일치한다. DB 근거 행 ID와의 매핑은 8번에서 결정한다.
- 같은 입력을 같은 문서 ID로 다시 처리하면 ID가 동일하다. 문서 ID나 블록 내용이 바뀌면 ID도 바뀐다.
- Chunk는 원래 블록·근거 ID를 재사용한다. Chunk의 오프셋을 원본 블록 오프셋처럼 쓰지 않는다.

### 형식별 동작과 한계

- PDF: 페이지/bbox·일반 텍스트·글꼴 기반 제목/코드 추정·격자 표를 추출한다.
  한글 텍스트 레이어도 테스트한다. 코드 들여쓰기는 글자 위치를 이용해 보존한다.
  복잡한 다단 배치, 선 없는 표, 모든 글꼴/배치의 완벽한 구조 인식은 보장하지 않는다.
- MD: CommonMark와 표 토큰의 줄 범위로 원문을 보존한다. 제목 계층, 코드 펜스·들여쓰기, 링크와 표를 유지한다.
- DOCX: 문단/표를 본문 순서로 읽는다. 제목·코드 스타일, 병합 셀 원점과 중첩 표를 보존한다. 페이지 번호는 만들지 않는다.
- XLSX: 시트명·셀 범위·행 번호·열 좌표·병합 범위·수식·링크를 보존한다.
  빈 행으로 표 구간을 나누며 고정 컬럼명이나 첫 행의 의미를 가정하지 않는다. 수식은 실행하지 않는다.
  4번 재검증에서 크기 제한 검사 전 전체 Cell 객체를 적재하던 부분을 수정했다.
  읽기 전용 미리보기로 시트 범위를 먼저 검사하고 제한 안에서만 전체 구조를 읽는다.
- TXT: UTF-8(BOM 포함) 후 CP949를 시도하며 문단·표시된 섹션·로그와 줄 순서를 보존한다.
- 표의 text는 셀을 탭/줄바꿈으로 표현한 추출 뷰다. 원래 셀 값과 좌표도 metadata에 보존한다.
  인용 검증은 이 표현 방식과 원본 위치를 고려해야 한다.

### 실패·청킹·재실행 정책

- 전체가 텍스트 없는 PDF는 TEXT_UNAVAILABLE로 실패한다. OCR/비전·이미지 설명은 아직 구현하지 않는다.
  혼합 PDF는 읽지 못한 페이지와 미전사 이미지 경고를 보존한다. 후속 Gate가 이를 무시해서는 안 된다.
  공백 글자만 있는 페이지도 읽을 내용이 없는 것으로 표시한다.
- 빈 파일/콘텐츠, 잘못된 형식/인코딩, 손상 파일, 읽을 수 없는 경로는 사유를 구분한다.
- ParseOptions의 기본 로컬 제한은 파일 50MiB, Office 확장 데이터 200MiB, 시트 범위 200,000셀이다.
  호출자가 변경할 수 있으며, 외부 업로드 API의 합의된 제한이라는 뜻은 아니다.
- ChunkOptions.target_chars의 기본값은 6000자다. 토큰 한도가 아니라 조정 가능한 소프트 기준이다.
  표·코드·로그·문단 블록은 중간에서 자르지 않으며 첫 제목은 다음 블록과 함께 보관한다.
  기준을 넘는 블록은 oversized로 표시한다. 5번은 실제 요청 JSON과 지침의 문자 수로 별도 제한하며 잘라 보내지 않는다.
- Parse/Normalize/Chunk를 재실행하면 그 이후의 오래된 결과를 초기화한다.
  extraction_result/drafts/draft_skips와 이전 검증 판정/통과 목록을 초기화한다. 반면 Draft는 현재 extraction_result를 유지한다.
  같은 작업의 verification_history/retry_feedback은 재처리 상한과 피드백을 위해 유지한다.
- 현재 Parse 노드는 배치 중 하나가 실패하면 예외로 종료한다. 최종 작업의 부분 성공/SSE 정책은 8번에서 결정한다.
- 기존 API 경로·응답, DB·SQL은 변경하지 않았다. 제품 업로드/Storage/백그라운드/SSE 연결은 후속 범위다.

로더 API 참고: [pdfplumber](https://github.com/jsvine/pdfplumber),
[python-docx](https://python-docx.readthedocs.io/en/latest/api/document.html),
[openpyxl](https://openpyxl.readthedocs.io/en/stable/api/openpyxl.reader.excel.html),
[markdown-it-py](https://markdown-it-py.readthedocs.io/en/latest/using.html).

## 5번 구현 — Extract / Draft Card 분리

| 모듈 | 책임 |
|---|---|
| `extraction.py`, `nodes/extract.py` | 청크마다 0~N개 독립 경험을 추출하고 코드 소유의 근거를 연결 |
| `drafting.py`, `nodes/draft_card.py` | 경험별 초안 또는 생략 결과 작성. 실제 저장·활성화는 하지 않음 |
| `llm.py` | 교체 가능한 StructuredGenerator와 OpenAI/Ollama/Anthropic 어댑터, 단계별 모델·오류·클라이언트 수명 관리 |
| `prompts/extract.md`, `prompts/draft_card.md` | 추출과 작성 지시문. 원문 안의 지시문은 데이터로 취급 |
| `models.py` | LLM 응답용 Proposal, 근거가 연결된 ExtractedElements, KnowledgeCardDraft, DraftSkip |

### 추출과 근거 연결

- LLM 출력은 ExtractedExperienceFields 목록이다. evidence_index나 위치를 모델에게 생성시키지 않는다.
  JSON Schema의 동적 근거 맵을 전송하지 않고 코드가 원본 DocumentIR의 근거를 붙인다.
- 청크가 원본 블록과 같은지, 인용 ID가 해당 청크에 있는지 검사한다. 모르는 근거로 원본을 새로 만들지 않는다.
- 같은 청크/완전히 같은 추출 결과의 반복만 제거한다. 유사 경험의 의미적 병합·기존 카드 중복 관리는 구현하지 않는다.
- experience_id는 추출 내용과 근거로 만든 재현 가능한 후보 키다.
  같은 payload에서 재현되며 LLM 출력이 달라지는 재실행까지 DB 중복을 방지한다는 뜻은 아니다.
  실제 저장 멱등성은 8번에서 job/경험 단위 저장 계약과 함께 구현한다.
- 한 청크에서 여러 경험을 반환할 수 있고 문서의 결과는 경험 목록으로 수집한다.
  서로 다른 청크의 불완전한 경험을 자동 병합하지 않는다. 문맥 부족은 후속 재처리 정책에서 다룬다.

### 초안 작성 전 최소 조건 — 현재 구현 선택

- 추출에서 confirmed로 표시된 context와 조건부 본문 5개 중 하나 이상의 confirmed 값이 필요하다.
- confirmed/inferred 교훈 후보가 있거나, confirmed 접근과 결과가 모두 있어야 한다.
- 부족하면 모델 호출 없이 DraftSkip에 경험 ID·생략 코드·사유·원래 추출 결과를 보존한다.
  모델도 draft=null 및 skip_reason을 반환해 작성을 생략할 수 있다.
- 이 조건은 근거 없는 초안 작성을 줄이기 위한 기본 조건이며 Verification Gate를 대신하지 않는다.

### 작성과 불확실성 보존

- LLM은 title/summary/lesson/keywords만 작성한다. 기술 목록과 context~outcome은 코드가 연결한다.
- context~outcome은 confirmed 추출값만 그대로 복사한다. unknown/hypothesis/inferred/conflicted 값은
  확정 본문에 넣지 않지만 source_extraction 스냅샷에는 상태·값·근거를 그대로 남긴다.
- KnowledgeCardDraft에 source_extraction과 필드별 field_evidence_refs를 보존한다.
  도출하거나 다시 쓴 교훈은 lesson_status=inferred로 표시하며 기존 확정 교훈을 그대로 쓴 경우에만 confirmed다.
- 이 표시는 구조적인 안전장치다. 제목/요약/교훈의 주장과 원문의 의미 대조는 아래 6번을 따른다.
- 5번 재검증에서 context~outcome의 근거 배열을 해당 원래 필드의 근거 배열과 일치하도록 보완했다.
  존재하는 다른 근거 ID라는 이유만으로 잘못 연결된 근거를 허용하지 않는다.
- 다시 쓴 교훈은 기존 교훈 문장뿐 아니라 confirmed 경험 필드의 근거도 보존한다.
  원래 confirmed 교훈을 그대로 쓴 경우에는 그 교훈의 근거를 유지한다. 근거 추가가 의미 검증을 대체하지는 않는다.
- 공급자 JSON Schema는 null/unknown/빈 배열까지 명시하도록 모든 속성을 required로 보낸다.
  Python 내부 스키마의 기본값은 유지한다. 생략 때문에 실수로 빈 추출로 해석되는 가능성을 줄인다.
- 초안은 status=draft다. 점수·등급·활성화 여부·가짜 저장 ID·검증 시각을 생성하지 않는다.
- to_card_fields()는 기존 API/DB 본문 필드명만 반환하며 내부 근거/후보 메타데이터를 제외한다.
  DB ID·시각·근거 행 연결 및 선택 메타데이터는 실제 저장 계약에서 처리한다.
- Draft는 원래 State.extraction_result를 변경하지 않는다. 초안은 별도 복사본을 소유한다.
  노드를 재실행하면 현재 결과를 교체하며 이전 초안 목록에 무조건 추가하지 않는다.

### 모델 연결과 검증 범위

- LLM_PROVIDER=openai/ollama/anthropic으로 공급자를 선택한다. Extract는 EXTRACT_MODEL,
  Draft는 DRAFT_MODEL을 읽으며 파이프라인/State/근거 계약은 동일하다. 모델·클라이언트는 State에 넣지 않는다.
- 현재 설정은 OpenAI다. OPENAI_API_KEY는 사용자 환경변수에서 읽고 코드/State/공용 예시에는 보관하지 않는다.
  로컬 .env는 일반 설정만 가지며 Git에서 제외한다. 새 사용자 환경변수는 VS Code/서버 재시작 후 상속된다.
  OpenAI/Anthropic/KURE 키는 SecretStr로 마스킹하며 설정 검증 오류도 입력 값을 숨긴다.
  이것이 저장소 암호화를 뜻하지는 않는다. 기존 Supabase 클라이언트의 키 전달 계약은 변경하지 않았다.
  KURE_API_KEY는 LLM 공급자와 별개로 최종 pass 카드의 실제 KURE 임베딩에 사용한다.
  공용 설정 예시는 [로컬 실행 안내](devtools/README.md#환경-설정)에 두며 로컬 `.env`를 재생성하거나 커밋하지 않는다.
  같은 이름이면 프로세스 환경변수가 `.env`보다 우선한다. 환경변수 값에는 실제 키만 넣고 앞뒤 공백을 제거한다.
- Ollama를 선택하면 모델 서버 실행·모델 설치가 필요하다. 자동 설치·다운로드·추론 또는 유료 fallback은 없다.
- Anthropic으로 바꿀 때는 LLM_PROVIDER=anthropic과 두 모델명을 claude-sonnet-5-5로 변경하고
  ANTHROPIC_API_KEY를 설정한다. LLM_BASE_URL은 Ollama 전용이며 Anthropic 키를 해당 주소로 보내지 않는다.
  API 키는 SecretStr로 관리하며 저장소에 올리지 않는다. 빈 입력/생략 결과는 클라이언트를 만들지 않는다.
- VERIFY_RULES_ENABLED=true는 필수이며 false는 설정 오류다. 후속 사용자 결정으로 VERIFY_LLM_ENABLED=true가 기본이다.
  true로 켜면 설정된 VERIFY_MODEL(현재 gpt-6.1-sol)을 사용한다. 검증도 같은 공급자를 사용하며 자동 전환하지 않는다.
  검증 모델 누락/호출 실패는 needs_review이고 pass로 대신 처리하지 않는다.
- DOCUMENT_LLM_TIMEOUT_SECONDS=60, MAX_OUTPUT_TOKENS=4096, MAX_INPUT_CHARS=48000을 기본값으로 쓴다.
  뒤의 두 설정의 전체 이름은 DOCUMENT_LLM_MAX_OUTPUT_TOKENS와 DOCUMENT_LLM_MAX_INPUT_CHARS다.
  입력 문자 수는 토큰 한도가 아니며, 실제 요청 크기가 기준을 넘으면 INPUT_TOO_LARGE로 실패한다.
- 2026-10-30 최소 실제 연결 확인: Sol·Luna 각각 짧은 구조화 응답, Supabase 카드 ID 최대 1건 조회,
  KURE 문장 1개 임베딩이 성공했다. DB 쓰기는 없었다. KURE는 요청에서만 키 앞뒤 공백을 제거했으며
  사용자 환경변수 수정은 아직 하지 않았다. 실제 문서의 품질·비용 및 전체 파이프라인 검증과 구분한다.
  상세 범위와 한계는 [연결 확인 기록](devtools/README.md#최소-연결-확인-기록--2026-10-30)을 따른다.
- OpenAI는 공식 https://api.openai.com/v1 의 Responses API에 strict JSON Schema를 보낸다.
  store=false, truncation=disabled, SDK max_retries=0이며 온도/추론 강도를 임의 고정하지 않는다.
  거절/미완료를 텍스트 파싱보다 먼저 구분하고, 하나의 완료된 assistant 구조화 텍스트를 원래 모델로 검사한다.
- Ollama는 /api/chat의 format에 JSON Schema를 보내고 stream=false로 받는다.
  Anthropic은 Messages API의 output_config.format에 SDK가 변환한 스키마를 보낸다.
  모든 공급자의 출력은 원래 Pydantic 모델로 다시 검증한다. 원문은 user 입력 JSON으로 지침과 분리한다.
  출력 제한은 OpenAI max_output_tokens, Ollama num_predict, Anthropic max_tokens에 연결한다.
  앱에서 원문을 임의로 자르지 않는다. 로컬 모델의 컨텍스트 길이/메모리는 별도 실측해야 하며
  문자 수 상한이 Ollama의 토큰 한도나 서버 측 입력 처리까지 보장하지는 않는다.
  SDK 자동 재시도·유료 API 자동 fallback은 없다. 재처리 요청 제어는 아래 6번을 따른다.
- 거절·미완료·빈 출력·잘못된 구조·API 오류를 성공으로 처리하지 않는다.
- make_extract_node/make_draft_node로 클라이언트를 주입할 수 있다. 테스트는 가짜 클라이언트와 모의 HTTP를 사용한다.
  실제 StateGraph를 연결했으며 제품 백그라운드/API/SSE 연결은 후속 작업이다.
- 실제 유료 API 실행 및 실제 문서에 대한 의미 품질 평가는 이번 검증에 포함하지 않았다.

공식 근거: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs),
[Anthropic Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
[Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview).

## 초기 목표 재점검 — 2026-10-28

사용자 제공 「Lessons Learned 지식카드 파이프라인 개선 설계서」와 최신 확정 요구를 기준으로 대조했다.

| 설계 목표 | 현재 판단 / 이번 보완 |
|---|---|
| 개발 경험 0~N개, 형식·고정 컬럼에 독립 | 유지. 청크 간 경험 자동 병합은 미구현이며 문맥 손실 가능성은 남음 |
| Extract/Draft/최종 카드 분리 | 유지. Draft는 저장·활성화하지 않으며, Gate 통과 목록도 여전히 draft 객체임 |
| 필드별 근거와 불확실성 보존 | 유지. 잘못 연결된 필드 근거를 거부하고 Gate에서 원본 대조 추가 |
| 주요 주장/가설/충돌 확인 | 이전 5번만으로는 충족되지 않았음. 6번에 규칙 검사와 선택적 LLM 의미 검사 추가 |
| 공급자 교체 | OpenAI 기본 + Ollama/Anthropic 명시 선택. 사용자 승인 변경이며 임베딩 모델 교체를 뜻하지 않음 |
| LLM 검증 없는 실행 | 초기 설계의 의미 검증 전체를 규칙으로 대체할 수 없음. 확신하지 못한 결과는 needs_review |
| 문서 제목은 원래 파일명 | 파이프라인에서 유지. 공유 docs/schema.md의 'LLM 추출' 설명과의 차이는 여전히 담당자 확인 사항 |
| 기술명 정규화 | 현재 원래 문자열/목록을 보존함. 별칭·오타 자동 통합을 구현했다고 주장하지 않음 |
| API/DB 호환, 4중 임베딩, Save | 외부 계약은 변경하지 않음. 입력 생성과 KURE 임베딩은 연결. 저장/업로드·SSE는 후속 8번 |
| 3순위 품질 평가·자동 개선 | 점수/가중치/사후 개선/스케줄러 미구현 유지 |

위의 최소 초안 조건, 규칙 모드의 보수적 보류, 작업 단위 재처리 상한은 현재 구현 정책이다.
기존 명세가 세부 알고리즘까지 확정한 것으로 표현하지 않는다.

## 6번 구현 — MVP Verification Gate

| 파일 | 책임 |
|---|---|
| `verification/models.py` | VerificationResult/Issue, 옵션, 근거별 주장 판정, RetryRequest 내부 계약 |
| `verification/structure_checker.py` | mutable/model_construct 입력 재검사, 필수 구조·최소 조건 |
| `verification/evidence_checker.py` | 현재 Extract 스냅샷 및 실제 DocumentIR 문서/블록/위치/인용 구간 대조 |
| `verification/consistency_checker.py` | 가설 강화·충돌·리터럴 일치, 선택적 LLM 의미 대조 |
| `verification/retry.py` | 판정 우선순위, 입력 fingerprint와 작업 단위 재처리 상한 |
| `verification/gate.py`, `nodes/verify.py` | 검사 호출과 경험별 결과 분리. 노드 본문은 검사 모듈을 호출 |
| `verification/reset.py` | 상위 노드 재실행 시 오래된 검증 통과 목록 제거 |
| `prompts/verify.md` | 원문과 주장·사실 상태 비교 지시. 점수·카드 활성화 지시 없음 |

### 판정과 검사 범위

- `pass`: 구조·최소 조건·원문 근거 검사와 선택한 일치 검사 방식에서 문제를 발견하지 않은 초안.
  `verified_drafts`에만 넣는다. 자동으로 active로 변경하거나 저장하지 않는다.
- `retry`: 수정 가능한 실패. issue에 code/check/field/reason/action/retry_node를 보존한다.
- `reject`: 최소 문제 해결 정보 부족 등 생성 부적절. 통과/검토 목록에 넣지 않는다.
- `needs_review`: 근거 충돌, 읽지 못한 원본, 의미 불확실, 검증 모델 오류/미설정, 재처리 상한/동일 입력.
  `review_drafts`로 분리한다. 현재 DB CardStatus에 새 상태를 추가하지 않는다.
- 여러 문제가 있으면 reject → needs_review → retry 순으로 보수적으로 판정한다.
  재처리가 여러 단계에 걸치면 parse → normalize → chunk → extract → draft_card 중 앞 단계를 선택한다.
- 근거는 실제 DocumentIR에서 ID/문서/블록/location/excerpt를 대조한다. 문자 구간은 선택값을 유지하며
  있으면 Python 인덱스로 원문 구간을 검사한다. quote 자체는 공백·줄바꿈을 정규화하지 않는다.
- 현재 Extract에 없는 스냅샷, 변조된 후보 ID, 유효하지 않은 필드별 근거 연결을 통과시키지 않는다.
- 혼합 PDF 등의 읽기 경고는 검토 대상으로 보류한다. 자동 OCR/비전으로 재처리하지 않는다.

### 규칙 모드와 LLM 모드의 차이

- 명시적으로 LLM 검증을 끈 규칙 모드는 원문 전체/문장/줄 단위와 일치하는 주장만 리터럴 일치로 확인한다.
  일치 검사에서 공백만 맞추며, 주변 가설/반박을 잘라 확정 문장으로 쓰는 경우를 보수적으로 보류한다.
  기술명은 원문에 있는지를 확인한다. 실제 기술명 별칭 표준화나 임의 키워드 사실 검증은 수행하지 않는다.
- 원문과 다르게 일반화한 제목·요약·교훈은 대개 needs_review다. 규칙 검사는 모든 자연어 의미,
  원문에 명시되지 않은 모순, 모든 기술적 오류를 증명하는 알고리즘이 아니다. 이 제한을 숨기지 않는다.
- 현재 기본인 LLM 모드는 코드 검사를 먼저 통과한 후 원본 블록과 필드별 주장·Extract 사실 상태를 대조한다.
  supported/unsupported/uncertain/conflicted를 주장마다 받는다. 누락/중복/잘못된 근거 참조는 needs_review다.
  가설을 제목/요약에서 사실로 반복하거나 확정 원인에 가설 표시가 남아 있는 경우는 규칙에서도 차단한다.
- 명백한 주장 불일치는 담당 Extract/Draft 재처리 요청, 문맥 누락은 Chunk, 읽기 오류는 Parse 요청으로 보낸다.
  충돌을 재시도로 임의 해소하지 않는다. LLM 판단 자체의 오류 가능성도 실제 문서로 별도 평가해야 한다.
- 실제 모델 추론/비용·지연·정확도 평가는 이번 자동 테스트에 포함하지 않았다.

### 재처리 및 다음 단계 연결

- VERIFY_MAX_RETRIES 기본값 2, 허용 범위 0~5. 현재는 같은 작업 State 전체의 재처리 **요청 수** 상한이다.
  독립 카드마다 무제한으로 예산이 새로 생기지 않는다. 배치 순서에 따라 예산 배분이 달라질 수 있다.
- verification_history는 이미 발행한 요청이고 retry_requests는 이번 요청이다. 현재 초안/원본/Extract의
  fingerprint가 같은 재처리는 재발행하지 않으며 같은 입력으로 LLM 검증도 반복 호출하지 않는다.
- Extract/Draft는 대상과 경험이 맞는 retry_feedback을 입력으로 받는다. 피드백을 새 원문 사실로 취급하지 않는다.
  실제 출력이 달라져야 의미 있는 재검사가 된다. 무작위 nonce나 시각만 바꿔 재처리로 간주하지 않는다.
- Parse/Normalize/Chunk/Extract/Draft 재실행은 이전 verified_drafts/검증 판정을 지운다.
  같은 작업의 이력/피드백은 유지한다. 새 작업은 새 State로 시작해야 한다.
- **실제 LangGraph 조건부 재처리 실행을 연결했다.** prepare_retry가 대상 입력을 복원하여
  Parse/Normalize/Chunk/Extract/Draft 중 지정된 노드로 보내고 다시 Verify한다.
  Chunk 요청만으로 문맥 확장 전략이 자동 생성되지는 않는다. 동일 결과이면 다음 판정에서 보류한다.
- graph.py는 StateGraph 조립만 담당한다. execution.py가 원본/결과/재처리 큐를 관리하며,
  nodes/initialize.py, select_candidates.py, settle.py, prepare_retry.py, finish.py가 각각 하나의 보조 노드를 노출한다.
  State의 all_*/settled_*는 재처리로 다른 경험의 결과를 잃지 않기 위한 작업 내부 데이터다.
- Draft 재처리는 해당 경험만 대상으로 한다. 앞 단계 재처리는 필요한 문서 범위의 콘텐츠를 다시 처리한다.
  재발견한 settled/다른 pending 경험의 동일 후보 ID는 건너뛴다. 의미가 비슷한 새 ID를 자동 병합하지 않는다.
  원본이 변경되면 그 문서에 의존한 이전 판정을 needs_review로 내려 오래된 pass를 유지하지 않는다.
- 재처리에서 후보가 0개면 reject/RETRY_NO_DRAFT로 사유를 남긴다. 파싱/모델 호출 예외는 그래프 오류로 전파한다.
  prepare_retry의 실행 문맥이 없으면 needs_review다. 같은 내용의 판단 fingerprint는 다른 경험이
  배치에서 빠졌다는 이유만으로 달라지지 않도록 대상 경험·참조 문서·스냅샷 일치 여부로 계산한다.
- 그래프 recursion_limit은 100으로 두고 Gate의 최대 재처리 요청 0~5가 실제 비용/횟수 상한을 제어한다.
  checkpointer 주입 및 verify 직전 중단/동일 thread 재개를 테스트했다. Agent Server에서는 서버가 체크포인트를 소유한다.
- langgraph.json에 documents(실제 설정)와 documents_demo(명시적 예제 응답)를 등록했다.
  로컬 화면 /dev는 실제 컴파일된 그래프와 실제 실행 노드 기록을 제공한다. 제품 API 경로와는 별도다.
  데모는 실제 모델 추론이나 DB 저장을 하지 않으며, 실행 스크립트는 브라우저 자동 조작 없이 서버만 시작한다.
  실행 방법은 [devtools/README.md](devtools/README.md), 팀 연결 계약은 [regulations.md](regulations.md)를 본다.
  제품 작업 스케줄러/업로드/SSE와 영속 저장은 아직 연결하지 않았다.
- Embed와 후속 Save는 원래 drafts가 아닌 verified_drafts만 받아야 한다.
  부분 저장 실패/영속 이력/카드 상태 매핑은 8번 담당 범위이며 이번에 DB를 변경하지 않는다.
- Gate의 내부 판정/이력은 quality_score/last_verified_at/verification_version에 자동으로 쓰지 않는다.
  3순위 Quality Evaluator/Card Improvement는 구현하지 않았다. 향후 검사 모듈과 결과 계약을 재사용할 수 있다.

## 어떤 입출력과 호출 계약이 미정인가

계약은 “누가 무엇을 넘기고, 무엇을 돌려주며, 실패는 어떻게 알리는가”에 대한 약속이다.
아래 예시 필드는 설명용이며 확정 필드가 아니다.

| 경계 | 이미 정해진 책임 | 아직 정할 구체적인 내용 |
|---|---|---|
| documents service → graph/parse | 상위 service가 업로드 후 파이프라인 실행 | 문서 ID·원래 파일명 외에 Storage 위치, bytes, 로컬 경로 중 무엇을 전달할지; 호출 함수명과 async 여부 |
| parse → normalize → chunk | DocumentIR와 원문 블록/근거 ID를 보존 | 기본 구조는 위와 같이 구현. OCR/비전, 복잡한 PDF 구조 및 청킹 고도화는 후속 |
| chunk → extract | 청크와 원문 ID로 0~N개 경험 추출 | 실제 모델명/계정 설정과 토큰 한도, 청크 간 문맥 확장/경험 병합 정책 |
| extract → draft_card | 경험별 초안·근거/상태 보존, Gate 대조와 실제 재처리 연결 | 실제 모델의 의미 품질 실측, 청크 문맥 보완 전략 |
| verify → graph/save | 네 판정과 원인별 조건부 재처리·한도·피드백 구현 | DB 카드 상태 매핑, 검토 대기 영속 보관, 실제 Save 서비스 연결 |
| save → knowledge_cards service | 다른 도메인의 service를 통해 카드와 근거 저장 | 진짜 함수명, 카드/근거를 한 번에 받을지, 단건/배치, 성공 시 ID·건수·실패 정보, 예외/async 여부, 원자성·재시도 중복 방지 책임 |
| graph → documents service | 진행과 결과를 상위 진입점에 전달 | 최종 결과 필드, 일부 문서 실패·카드 0개 처리, 진행 이벤트 콜백/저장 방식, 작업 실행·구독 계약 |

따라서 “저장할 곳이 전부 미정”이라는 뜻이 아니다.
임베딩의 네 가지 구성과 대상 필드는 첨부 PDF 및 사용자 설명으로 정해졌다.
벡터의 실제 저장 구조·모델·차원, 그리고 필요 시 도입할 작업 진행 상태의 지속 저장 방식은 미정이다.
파싱 결과 캐시·임시 파일 보관도 필요성과 정책이 별도 결정되지 않았다.

### 이미 있는 DB 제약과 아직 없는 정책을 구분하기

- 카드의 `title`, `summary`, `context`, `lesson`은 필수이며 빈 문자열 불가다.
- `problem`, `cause`, `approach`, `rationale`, `outcome` 중 하나 이상은 공백 아닌 값이어야 한다.
- 카드당 근거 문서 하나 이상을 service가 보장한다. 한 문서의 여러 근거도 가능하다.
- 카드 상태의 허용 값은 `draft`, `active`, `flagged`, `archived`다.
- MVP Verification Gate 판정은 구현했다. DB 카드 상태 매핑과 검토 대기 영속 저장 여부는 8번에서 결정한다.
  판정과 DB의 카드 상태는 다른 개념이다. 품질 점수 계산·가중치·사후 자동 개선·정기 재평가는 3순위다.
- quality_score/last_verified_at/verification_version은 기존 nullable 필드로 유지한다.
  실행하지 않은 3순위 평가 결과를 채우지 않는다. Gate 정보와의 관계는 후속 저장 계약에서 결정한다.
- 작업 진행 상태와 카드 상태도 서로 다른 개념이다. 카드 상태 컬럼을 작업 진행 저장소로 쓰지 않는다.
- needs_review는 Gate 판정이며 현재 CardStatus에 같은 값을 추가하지 않는다.
  검증을 통과한 active 카드와 구분한다. 검토 대기 결과를 어디에 보관할지는 6/8번에서 결정한다.
- 임베딩은 최대 4개이며 각 관점의 입력 텍스트 최소 생성 조건은 아래 7번 구현 기본 정책을 따른다.
  누락 정보를 채워 넣지 않는다. 카드·근거·생성 가능한 벡터 저장의 부분 실패/재시도는 8번에서 처리한다.

## 가짜 카드 저장 함수 — 임시 구현

`pipeline/fake_function.py`에 `async fake_save_cards(cards)`를 구현했다.
현재 입력은 카드 payload mapping의 목록이며 내부 필드를 검사하거나 바꾸지 않는다.
반환값 `FakeSaveResult`는 받은 카드 수, `is_fake=True`, 실제 생성 수 0, 빈 저장 ID 목록이다.
여기서 입력 payload의 최종 필드나 실제 서비스의 반환 형식을 확정한 것은 아니다.

향후 연결 제안: `nodes/save.py`가 조립 시 바인딩된 저장 함수를 호출하게 하고,
가짜/실제 구현을 조립 단계에서 명시적으로 선택한다. `graph.py`에는 조립만 남긴다.
그래프에 등록되는 노드는 `(state) -> 부분 업데이트 dict` 계약을 유지하며,
저장 함수를 State 데이터 필드로 넣지 않는다. State 필드 미정 때문에 이번에는 이 연결을 구현하지 않았다.

- 임시 파일 주석에 “DB에 저장하지 않음, 어떤 실제 함수로 교체할지, 아직 미정인 계약”을 적는다.
- 가짜 저장 결과를 실제 DB 저장 성공이나 실제 생성 카드 수로 보고하지 않는다.
- 가짜/실제 구현 선택은 명시적으로 한다. 실제 저장 함수가 없거나 실패했다고 조용히 fake로 대체하지 않는다.
- router에 넣지 않는다. 카드 테이블에 직접 쓰는 코드도 넣지 않는다.
- 카드+근거 저장 도중 일부만 성공했을 때의 처리와 재시도 중복 방지는 실제 서비스 담당자와 합의한다.
  현재 함수가 없으므로 원자성이 보장된다고 주장하지 않는다.

## 7번 — 임베딩 입력 생성과 실제 KURE 호출 / DB 저장은 후속

출처는 사용자가 첨부한 「지식카드는 어떻게 임베딩할 것인가?」 PDF(2026-10-27)다.
하나의 카드 ID에 전체 맥락·상황과 문제·대응과 해결·교훈이라는 네 관점을 연결한다.
구성 필드는 아래 표를 기준으로 한다. 논리적 이름이며 현재 DB에 해당 컬럼이 있다는 뜻은 아니다.

| 관점 / 논리적 이름 | 포함하는 지식카드 필드 |
|---|---|
| 전체 맥락 / `content_embedding` | `title`, `summary`, `context`, `problem`, `cause`, `approach`, `rationale`, `outcome`, `lesson` |
| 상황 및 문제 / `situation_embedding` | `context`, `problem` |
| 대응 및 해결 / `solution_embedding` | `problem`, `cause`, `approach`, `rationale`, `outcome` |
| 교훈 / `lesson_embedding` | `context`, `problem`, `lesson` |

표의 `title`은 지식카드 제목이다. 원본 문서의 제목은 사용자가 요청한 대로 원래 파일명을 유지한다.
이 문서와 실제 스키마의 제목 설명 차이는 담당자와 정리할 미정 항목이다.

원본 문서를 네 조각으로 자르는 청킹 정책과 혼동하지 않는다.

### 자료에서 확정한 사항

- 각 관점의 입력은 생성된 지식카드의 필드를 조합한다. 역할 이름을 붙여 의미를 명확히 한다.
- 상황/문제에는 `cause`를 넣지 않고, 대응/해결에는 `cause`를 넣는다.
- `technologies` 전용 벡터는 만들지 않는다. 기술명은 키워드/메타데이터 필터 중심으로 처리한다.
  다른 본문 필드에 들어 있는 기술 관련 맥락을 임베딩 입력에서 제거한다는 의미는 아니다.
- 검색 시 질문에 맞는 관점을 고르거나 여러 관점을 함께 사용할 수 있다. 챗봇 구현은 타 담당이다.

임베딩 구현 전 결정 항목은 [checklist.md](checklist.md)의 해당 절에서 관리한다.

### 이번 구현의 기본 정책 — 원래 설계의 필드 조합과 구분

원래 설계는 관점별 필드 조합과 최대 4개라는 원칙을 정했으나 최소 생성 조건은 열어 두었다.
아래 조건은 이번 구현에서 정한 `card-views-v1` 기본 정책이다. 팀의 DB/검색 계약이 확정됐다는 뜻은 아니다.

| 관점 | 최소 생성 조건 | 나머지 구성 필드 |
|---|---|---|
| content | title + summary + context + lesson, 그리고 problem/cause/approach/rationale/outcome 중 1개 이상 | 존재하는 값만 포함. 기존 카드의 최소 본문 요건과 동일 |
| situation | context + problem | 두 필드만 사용 |
| solution | problem + approach | cause/rationale/outcome은 있을 때만 사용. 원인이 미확인이라는 이유만으로 해결 방법을 버리지 않음 |
| lesson | context + problem + lesson | 세 필드만 사용 |

문제 없이 접근·결과만 있는 카드도 기존 카드 요건을 충족하면 content는 생성할 수 있다.
problem이 필요한 다른 관점은 `INSUFFICIENT_FIELDS`와 누락 필드를 남긴다.
이는 카드 자체의 Gate 판정이나 DB 상태를 바꾸는 동작이 아니다.

### 모듈·입출력·재실행

- `embedding_texts.py`: 순수 함수 `build_embedding_texts(draft, options=...)`와 내부 결과/옵션 모델.
  `nodes/build_embedding_texts.py`: 최종 pass 판정이 있는 verified_drafts만 함수에 전달한다.
  판정 누락·비통과·중복 경험/판정이 섞인 잘못된 입력은 오류로 중단한다.
- 그래프는 모든 재처리 결과를 `finish`에서 모은 뒤 `build_embedding_texts`를 실행한다.
  재처리 도중의 임시 pass나 needs_review/reject 카드는 입력으로 사용하지 않는다.
- State의 `embedding_texts: list[CardEmbeddingTexts]`는 경험별 결과다. 각 결과는 experience_id,
  template_version, 생성 가능한 관점의 texts와 생성 불가 관점의 skipped를 가진다.
  texts의 각 항목은 text/included_fields, skipped는 오류 코드·누락 필드 또는 초과 글자 수를 보존한다.
  experience_id는 내부 경험 식별자이며 DB 카드 ID나 저장 성공을 뜻하지 않는다.
- 템플릿은 설계 표 순서대로 `[필드명]` 줄과 원래 값을 쓰고, 필드 사이에 빈 줄 하나를 넣는다.
  원래 값의 공백·줄바꿈·코드·로그를 보존한다. 문서 파일명·인용문·근거 ID·키워드·기술 태그는 덧붙이지 않는다.
  구성 필드·라벨·순서·최소 조건을 바꾸면 템플릿 버전도 올려 후속 저장/검색에서 구분해야 한다.
  카드 본문에 이미 있는 기술명은 유지한다. 카드 title과 원본 문서 파일명은 계속 구분한다.
- Extract 스냅샷에서 누락 필드를 다시 채우지 않는다. KnowledgeCardDraft를 재검증하고 카드 본문만 조합한다.
  원본 사실 상태·필드별 근거·lesson_status는 초안에 그대로 남으며 텍스트 생성이 이를 확정 사실로 바꾸지 않는다.
- `embedding_text_options.max_chars`는 호출자가 지정할 수 있는 양의 정수 글자 수 제한이며 기본 None이다.
  초과 관점은 `INPUT_TOO_LARGE`로 보류하고 자르거나 요약하지 않는다. 토큰 한도라는 뜻은 아니다.
  실제 KURE의 토큰 한도·배치·입력 유형 검사는 아래 호출 구현을 따른다.
- 재실행은 결과를 덮어쓰며 새 작업·상위 노드 재실행·재검증·재처리는 이전 입력 텍스트를 초기화한다.
  클라이언트·모델·비밀키는 State에 추가하지 않았다.
- 실제 KURE 호출과 벡터 결과를 아래 후속 구현에서 연결했다. DB 필드·검색 API·품질 점수는 추가하지 않았다.
  기본 2,048차원은 실행 설정이며 최종 DB/검색 계약으로 고정하지 않는다.

### 초기 목표 대조 결과

원본 첨부 설계서의 `Verification Gate → Build Embedding Texts → Embed → Save` 중
Build Embedding Texts와 실제 Embed까지 연결했다. 다섯 문서 형식에 독립적인 카드 입력, 0~N개 경험,
근거·가설 보존, 누락 내용 비생성, 기존 API/DB 호환성과 3순위 품질 평가 제외 원칙을 유지한다.
원본 청크를 네 부분으로 나누거나 모든 카드에 네 벡터를 강제로 생성하는 구조로 바꾸지 않았다.

입력 생성 단계 검증 기록: `tests/test_document_embedding_texts.py`의 신규 25개를 포함해 당시 전체 231개 테스트가 통과했다.
Ruff lint와 95개 Python 파일의 포맷 검사도 통과했다. 실제 API 호출·DB 쓰기 없이 검증했다.
관점별 정확한 조합·누락/길이 초과·한글/코드/로그 보존·가설 원인 비포함·추론 교훈 상태 보존,
여러 독립 경험과 규칙 모드 검토 대기 제외·조건부 재작성 후 최종 입력·체크포인트 재실행을 포함한다.

### KURE 클라이언트 연결과 입력 재검토

- 입력 생성의 네 관점 조합과 기존 최소 조건은 원본 설계와 일치해 유지했다.
  생성 시점의 검사를 공통 함수로 분리하고, embed 직전에 현재 Gate pass/초안/옵션으로 다시 조립해
  State의 텍스트와 비교한다. 오래됐거나 변조된 입력은 `EMBEDDING_STALE_INPUT`으로 요청 전에 거부한다.
- `embeddings/models.py`: 교체 가능한 EmbeddingClient 계약, 공통 모델 설정·배치 결과·사용량·카드 벡터 모델.
  `embeddings/kure.py`: 공식 HTTPS 주소로만 요청하고 스냅샷 모델·응답 수·index·차원·유한 수·0 벡터를 검사한다.
  `embeddings/service.py`: 최대 8개 순차 배치와 경험/관점별 벡터·입력 SHA-256 해시 연결.
  `nodes/embed.py`: 입력 재검토와 클라이언트 호출만 담당하며, 생성할 관점이 없으면 키/클라이언트 없이 종료한다.
  `llm.py`는 클라이언트 생성·종료, `graph.py`는 build_embedding_texts → embed 연결만 담당한다.
- 기본값은 EMBEDDING_MODEL=kure-v3-20261016, EMBEDDING_DIMENSIONS=2048,
  EMBEDDING_BATCH_SIZE=8, EMBEDDING_TIMEOUT_SECONDS=30이다. settings와 로컬 `.env`/공용 예시에 반영한다.
  KURE 클라이언트는 변동 가능한 별칭을 허용하지 않는다. 공통 인터페이스는 다른 공급자/모델도 표현하며
  명시적으로 주입한 클라이언트+모델 설정으로 교체 가능하다. 자동 대체 모델이나 가짜 벡터는 없다.
- 요청은 input_type=document, encoding_format=float를 사용한다. 모델용 passage 접두어를 직접 붙이지 않는다.
  키의 앞뒤 공백을 제거하고 빈 키/내부 공백은 거부한다. 환경변수 원본은 수정하지 않는다.
  키·클라이언트는 State에 넣지 않으며 오류에 서버의 원문 메시지나 키를 포함하지 않는다.
- 8,192토큰 한도는 서버가 검사한다. 글자 수를 토큰으로 근사하거나 임의로 잘라 보내지 않는다.
  400 context_length_exceeded는 EMBEDDING_INPUT_TOO_LARGE로 실패한다. 로컬 tokenizer 다운로드는 없다.
- 401/403, 모델 없음/폐기, 429, 5xx, 연결 실패·시간 초과를 구분한다.
  자동 재시도는 없으며 재시도 가능 여부와 숫자 Retry-After만 전달한다. Verify의 재처리 예산과 별개다.
- State의 embeddings에는 생성된 경험별 관점 벡터와 provider/model/dimensions/input_type/template_version/
  입력 해시를 보존하고, embedding_usage에는 실제 배치별 토큰 사용량을 보존한다. 카드 ID·저장 성공을 생성하지 않는다.
- 모든 배치가 성공한 뒤 한 번에 결과를 반환한다. 뒤 배치가 실패하면 일부 벡터를 성공으로 공개하지 않는다.
  노드 재시도 시 앞선 성공 배치가 재호출될 수 있다. 영속 캐시/배치 복구는 아직 없으며 과금·사용량이 중복될 수 있다.
  완료한 노드의 체크포인트 재개는 재호출하지 않는다. 새 작업·상위 재실행·재검증·입력 생성은 오래된 벡터/사용량을 지운다.
- documents_demo는 embed를 명시적으로 생략하고 가짜 벡터를 생성하지 않는다. documents는 실제 KURE를 호출한다.
  규칙 모드를 명시적으로 선택해 needs_review만 나온다면 임베딩 호출이 없는 것이 정상이다. 검증 통과 없이 강제로 호출하지 않는다.
- 실제 확인: 합성 카드 1개/네 관점/요청 1회로 kure-v3-20261016의 2,048차원 벡터를 받았다.
  입력 137토큰이며 OpenAI 호출/DB 쓰기는 없었다. 실제 문서 품질/전체 제품 연결 확인과 구분한다.
- 클라이언트 연결 후 검증: 신규 46개를 포함한 전체 277개 테스트와 Ruff lint/100개 Python 파일 포맷 검사를 통과했다.
  요청·인덱스/차원/벡터·모델 버전·긴 입력 오류·인증/할당량/연결 실패·부분 배치 실패·입력 변조를 검사했다.
  실제 그래프의 최종 재작성 후 호출과 완료 체크포인트 재개 시 중복 호출 방지, 클라이언트 교체도 모의 HTTP로 확인했다.
  `.env`의 숫자 문자열이 차원·배치·시간 제한으로 읽히는 회귀 테스트와 실제 로컬 설정 로딩 확인도 포함한다.

공식 계약: [KURE 임베딩 API](https://kure.kullm.ai/en/docs/embeddings),
[모델·스냅샷](https://kure.kullm.ai/en/docs/models), [오류·제한](https://kure.kullm.ai/en/docs/limits).

## 독립 리뷰 후속 — LLM 의미 검증 기본 활성화와 합성 검증

2026-10-30 사용자가 LLM 의미 검증으로 진행하기로 결정하고 대표 문서 없이 가능한 검증을 요청했다.
기존 독립 검토 스냅샷의 HEAD는 1aa179d이며 이번 변경은 그 이후의 후속 작업 단위다.
기존 독립 검토 문서는 당시 스냅샷으로 보존하고 후속 변경 안내를 붙였다.

### 확정·반영한 정책

- `app/config.py`의 VERIFY_LLM_ENABLED, 내부 VerificationOptions.llm_enabled, 로컬 `.env` 플래그를 true로 맞췄다.
  규칙 검사는 여전히 필수다. 구조·근거·누락 경고 등 선행 검사를 통과한 초안만 현재 VERIFY_MODEL로 의미 검증한다.
- title/summary 검사를 근거 링크 존재만으로 완화하지 않았다. 원문과 다른 사실 필드·교훈도 의미 검증의 대상이다.
- supported면 통과할 수 있고 unsupported는 대상별 재처리, uncertain/conflicted·모델 오류는 보류한다.
  모델이나 키가 없을 때 규칙 통과로 자동 대체하지 않는다.
- 명시적 규칙 모드(false)는 유지하며, 표현이 달라 의미 확인이 필요한 주장은 needs_review로 남긴다.
- PDF 경고, 부정·반박식, 일부 파일/청크 실패, Embed 실패의 범위와 차원은 이번에 변경하지 않았다.

### 대표 문서 없이 수행한 검증

신규 파일 `tests/test_document_gate_policy.py`의 19개 사례를 포함해 전체 296개 pytest와 Ruff lint/101개 Python 파일 포맷 검사를 통과했다.
기존 Writer, 기존 의미 검증/길이 오류 분류 테스트는 수정하지 않았다. 이번 검증의 외부 API 호출·DB 쓰기는 0회다.
합성 문장은 테스트용 최소 예시이며 데이터셋 생성 기능이나 실제 대표 문서 평가가 아니다.

| 대상 | 검증 방법 | 관측 결과와 한계 |
|---|---|---|
| 기본 설정 | 설정 기본값과 실제 verify 노드의 모의 클라이언트 연결 | LLM 의미 검증이 기본 활성화되고 verify 단계로 전달됨. 실제 API/모델 품질은 측정하지 않음 |
| 표현이 다른 제목·요약·사실·교훈 | 한글 원문과 바꿔 쓴 Extract/Draft, 사전 지정한 모의 의미 판정 | pass/retry/needs_review와 재처리 대상, 오류 보류 및 명시적 규칙 모드의 보류 확인 |
| PDF 이미지 경고 | 실제 ReportLab 최소 PDF → Parse/Normalize/Chunk/Extract/Draft/Gate/후속 노드. 이미지 없음/본문 작은 이미지/부록 작은 이미지 3종 | 이미지 없는 대조군은 모의 의미 검증 통과. 작은 이미지가 본문 또는 부록에 있으면 1쪽 근거 카드도 INCOMPLETE_SOURCE로 보류되며 의미 모델 호출 전 차단됨 |
| 한글 부정/반박 | 관련 없는 부정문·중립문·블록 전체 부정문·인접 가설·반박 5종 | '캐시에는 문제가 발생하지 않았다'가 다른 원인 문장의 규칙 매칭까지 막는 사례 재현. 블록 전체 일치의 부정문은 통과할 수 있음. 일반 문서 과보류 비율은 아님 |
| 긴 임베딩 관점 1개 | 테스트 전용 문자 제한 클라이언트에 관점 4개 전달 | 하나만 길어도 현재 배치 전체 실패함을 확인. 명시적 max_chars로 content를 보류하면 다른 3개를 처리할 수 있음. 실제 토큰 계산/실패 입력 자동 식별 기능은 아님 |

모의 모델이 supported라고 답하도록 지정했기 때문에 '실제 LLM이 이 문장을 올바르게 이해한다'는 증거는 아니다.
위 부정식·PDF 테스트는 바람직한 최종 정책을 확정한 것이 아니라 현재 문제를 재현한 특성화 검사다.
대표 문서 보류율·잘못 통과한 주장·실제 의미 검증 지연·비용 평가는 여전히 미완료다.

### 이후 사용자/팀 결정이 필요한 사항

| 결정 | 현재 유지한 동작 | 결정할 내용/협의 영역 |
|---|---|---|
| PDF 미전사 경고의 영향 범위 | 문서 경고가 참조 카드 전체를 보류 | 페이지·영역·내용 누락/장식 이미지 구분과 카드별 적용. 작은 이미지라는 이유만으로 무시할지 단정하지 않음 |
| 파일·청크·경험의 부분 실패 | 예외가 전체 실행으로 전파 | 정상 항목을 계속 처리할지, 불완전 문서 표시·job/SSE 최종 상태·재시도 범위를 documents/프론트와 결정 |
| 임베딩 관점별 실패 | 노드 전체 실패, 자동 분리/재시도 없음 | 실패 입력 식별 방식·추가 호출 상한·일부 벡터 누락 카드 저장/공개 허용을 검색/저장 담당과 결정 |
| needs_review 영속 보관 | State의 review_drafts에만 유지 | draft/flagged/별도 검토 결과 중 저장 방식, 검토자·재검증·보존 정책을 knowledge_cards/DB 담당과 결정 |
| 실제 모델 비용 예산 | 요청별 한도와 작업 전체 Gate 재처리 상한 2 유지 | 작업당 총 호출/토큰·비용 상한과 실패 시 처리. 의미 검증은 선행 검사 통과 초안마다 추가 호출됨 |
| 벡터 저장·검색 | 기본 2,048차원, DB 저장 없음 | 차원 축소 또는 halfvec 등 저장/인덱스·검색 RPC·카드 ID/버전 계약을 DB/검색 담당과 함께 결정 |

대표 문서가 확보되면 같은 Extract/Draft를 고정한 Gate 비교와 실제 전체 흐름 평가를 분리할 수 있다.
통과율 증가만 목표로 삼지 않고 잘못된 통과·불필요한 보류·근거 일치·호출량/지연을 함께 평가해야 한다.

## 문서별 완료·정리와 청크별 재시도 — 2026-10-30 후속 구현

사용자가 승인한 후속 방향이다. 위 독립 리뷰 후속 표의 '일부 파일/청크 오류는 전체 전파'는
당시 동작이며, 현재 그래프는 아래처럼 변경했다. 외부 API·DB 스키마 계약은 변경하지 않았다.

- `processing.py`는 내부 처리 옵션, 청크/문서 진행 상태, 처리 오류, 저장 요청/확인 모델을 정의한다.
- Parse는 파일별 ParseError를 기록하고 정상 문서를 계속 처리한다. 코드 결함 등의 예기치 않은
  예외까지 전부 삼키지는 않는다. 직접 사용하는 파서 함수의 예외 계약은 그대로다.
- `prepare_extraction → extract → extract ... → merge_extraction`으로 실행한다.
  extract 한 번에 청크 하나와 호출 시도 하나를 처리하므로, 결과가 다음 호출 전에 State에 반영된다.
  체크포인터를 연결한 같은 thread의 재개는 `None` 입력으로 수행하며, 성공한 청크를 다시 부르지 않는다.
- `ProcessingOptions.max_chunk_attempts`는 최초 호출 포함 기본 3회/최대 5회다.
  기본 대기는 1초, 이후 지수적으로 증가하며 최대 30초다. INPUT_TOO_LARGE, 근거 오류,
  설정/인증 오류 등은 자동 반복하지 않는다. 공급자 HTTP 408/429/5xx와 연결·시간 초과 등을
  제한적으로 재시도하며, 기존 Gate의 작업 전체 의미 재작성 예산과 분리한다.
- 청크 키는 원본 IR, 청크, Extract 프롬프트, 재처리 피드백에 의존한다. 새 작업에서는 캐시를
  초기화한다. 모델/설정·코드가 바뀐 작업을 예전 체크포인트로 재개하는 것은 지원 계약이 아니다.
- Gate가 Extract를 요청하면 해당 경험의 근거 블록을 포함한 청크만 다시 추출한다.
  상위 Parse/Normalize/Chunk 재실행은 해당 문서의 추출 캐시를 무효화한다.
  Extract만 재실행했을 때 다른 청크의 원본 목록을 잃지 않도록 보존한다.
- Draft 호출 오류는 경험별로 기록한다. 해결되지 않은 청크·Draft 오류가 있는 문서의 통과 후보는
  임베딩/저장 대상으로 내보내지 않는다. 해당 후보는 근거와 함께 재검토용 임시 상태에 남는다.
- 최종 후보를 모은 뒤 문서별 `embed → save → complete_document`를 실행한다.
  문서 하나의 Embed/Save 실패는 다른 문서 결과를 지우지 않는다. 한 문서 안의 관점별 KURE
  배치 실패는 여전히 문서 전체 실패이며, 긴 입력 자동 식별·분할은 이번 구현이 아니다.

### 저장 완료와 정리의 경계

`DocumentProgress.status`는 내부 작업 상태이며 DB 카드 상태나 SSE 이벤트를 새로 정한 것이 아니다.
`failed` / `incomplete` / `needs_review` / `ready_to_save` / `completed`를 구분한다.

실제 지식카드 저장 서비스는 아직 없다. `build_graph(document_saver=...)`로 내부 어댑터를
주입할 경계만 추가했고, 기본값은 미주입이다. fake_save_cards는 연결하지 않았다.
미주입 시 저장 확인을 생성하지 않으며 결과는 ready_to_save에 머물고 임시 데이터도 보존된다.

저장 어댑터는 `DocumentSaveRequest`의 문서 IR, 통과 카드(필드별 근거 포함), 벡터,
검증 결과와 생략 사유를 받아 `knowledge_cards.service`를 통해 저장해야 한다.
카드·근거·필요한 벡터와 문서 완료 기록을 영속 저장한 뒤에만 SaveReceipt를 반환한다.
문서 ID·요청 해시·카드 ID 개수/중복·명시적 committed/result_recorded를 확인한다.
입력 본문 전체의 payload_hash는 요청 재사용을 위한 보조 식별자이며, 모델 출력이 바뀌어도
중복을 막는 영속 job/document/card 키를 대신하지 않는다. 실제 멱등성·트랜잭션은 C05 계약이다.

확인된 문서만 completed로 기록하고 현재 State의 청크·파싱/정규화 복사본·초안·입력·벡터를
정리한다. 작은 완료 요약/카드 ID·원본 파일 참조는 남기고 원본 파일이나 영속 카드/근거는 삭제하지 않는다.
실패·검토 대기 데이터는 지금 자동으로 삭제하지 않는다. 보관 기한 및 검토 자료의 별도 보관은
C03/C04/C09에서 정할 사항이며, 24시간 TTL이나 정리 스케줄러를 구현한 것은 아니다.

현재 State를 비워도 과거 LangGraph 체크포인트에는 본문이 남을 수 있다.
`release_completed_checkpoints`는 그래프 종료 후 모든 문서가 completed이고 상위 서비스에서
최종 job 결과를 따로 영속 기록했다고 확인한 경우에만 thread 이력을 삭제한다.
같은 thread에 미완료 문서가 있으면 전체 삭제를 거부한다. 이 함수의 제품 서비스 호출은 미연결이다.
따라서 이번 변경을 문서 완료 직후 과거 이력의 물리적 즉시 삭제까지 보장하는 것으로 해석하지 않는다.
청크별 체크포인트가 늘기 때문에 실제 제품 연결 시 저장량·보관 정책도 함께 확인해야 한다.

기본 그래프는 체크포인터를 만들지 않는다. Agent Server는 자체 영속성 설정을 따르며,
직접 실행자는 체크포인터를 주입해야 한다. 메모리 체크포인터의 테스트는 같은 프로세스에서의
중단·재개만 입증하며, 프로세스 재시작/다중 워커 복구는 영속 체크포인터와 제품 job 계약이 필요하다.
직접 호출에서 매 단계 기록 완료를 기다려야 한다면 영속 체크포인터와 `durability="sync"`를 함께 사용한다.
응답 수신 후 체크포인트 기록 전의 장애까지 외부 API 호출을 정확히 한 번으로 보장하는 것은 아니다.
그래프 기본 recursion_limit은 청크별 실행을 위해 1000으로 늘렸으며 큰 작업은 호출 설정을 따로 지정한다.

검증: 전체 pytest 318개 통과. 새 `tests/test_document_processing.py`는 선택적 청크 재시도,
인증/입력 오류의 비반복, 문서 실패 격리, 체크포인트 재개, 저장 확인 전 보존/확인 후 정리,
부분 완료 시 이력 삭제 거부, 저장 확인 직후 중단 재개와 예상치 못한 코드 오류의 전파를 확인한다.
실제 외부 API 호출·DB 쓰기는 수행하지 않았으며 저장 어댑터 검증은 테스트용 구현을 사용했다.

### 이번에 추가하지 않은 기능

OCR/비전 재읽기, PDF 경고 범위 완화, 실제 저장 서비스·마이그레이션, 제품 업로드/SSE,
실패 작업의 TTL 스케줄러, 소진된 재시도 예산을 수동으로 다시 여는 API,
품질 점수/사후 개선/정기 재평가는 이번 범위에 포함하지 않았다.

## 형식별 도구 선정 제안 — 채택 전 확인

먼저 각 형식의 파서가 돌려주는 구조와 원문 위치를 비교한다.
LangChain 로더가 필요한 정보를 보존하면 재사용하고, 표·셀·레이아웃 정보가 사라지면
해당 형식의 전용 파서를 얇은 어댑터로 연결하는 방법을 검토한다.
특정 라이브러리, OCR/비전 엔진, 임베딩 모델은 이 문서로 확정하지 않는다.
의존성을 추가하기 전 현재 버전·Python 호환성·라이선스·필요한 위치 추적 기능을 확인한다.

## 자동화 확인 — 설정을 변경한 것은 아님

2026-10-27 확인 결과이며 실제 변경 전 설정을 다시 확인한다.

| 규칙 | 현재 자동화 상태 |
|---|---|
| Python 포맷 | `.vscode/settings.json`에 Ruff 기본 formatter와 formatOnSave 설정. Ruff 확장 설치 확인 |
| import 순서 | Ruff `I` 규칙과 저장 시 `source.organizeImports: explicit` 설정. 명시적 저장 기준 |
| 린트 전체 자동 수정 | `source.fixAll.ruff` 설정 없음. 포맷/import 정리와 모든 린트 오류 수정을 혼동하지 않음 |
| 네이밍 | 팀 규칙은 있으나 Ruff 선택은 `E`, `F`, `I`. pep8-naming `N`은 선택되지 않음 |
| 커밋 메시지 | 팀 문구 규칙만 확인. 활성 Git hook·별도 hooksPath·commitlint·GitHub workflow 없음 |
| API 응답 | CamelModel 재사용 기반은 있음. 모든 route의 response_model·오류 모양을 강제하는 전용 검사 없음 |
| CI/커밋 전 검사 | `.github`에는 PR 템플릿만 있음. 체크박스는 자동 검사가 아님 |

네이밍 `N`, 커밋 메시지 hook/CI, API 계약 테스트 추가는 팀 공통 설정 변경 제안이다.
이 pipeline 문서 정리 작업에서 공통 설정을 바꾸지 않는다.

Ruff 저장 동작과 규칙 분류는 [공식 에디터 문서](https://docs.astral.sh/ruff/editors/features/)와
[공식 규칙 목록](https://docs.astral.sh/ruff/rules/)도 참고한다.
