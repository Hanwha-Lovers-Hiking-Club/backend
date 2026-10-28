# 문서 파이프라인 설계 메모

이 문서는 문서 파이프라인의 현재 상태, 확정된 설계와 미정 계약을 정리한다.
개발 진행 항목은 [checklist.md](checklist.md)에서 관리한다.
내용의 적용 범위는 pipeline 하위다.
외부 파일 경로는 연결 상대를 설명하며, 수정 권한을 넓히지 않는다.

## 확인된 상태 — 2026-10-28 로컬 기준

- `models.py`에 3번 작업의 `EvidenceRef`, `ExtractedField`, `ExtractedElements`를 구현했다.
  `ExtractionResult`로 0~N개 독립 경험을 표현한다.
- 4번의 형식별 파서, DocumentIR, 구조 기반 청킹, Parse/Normalize/Chunk 노드와 최소 State를 구현했다.
  전체 graph 연결과 실제 Extract/Draft/Verify/Embed/Save 실행은 아직 구현하지 않았다.
- `fake_function.py`에 개발용 `fake_save_cards`가 있다. DB에 저장하지 않는 독립적인 가짜 함수다.
  그래프·save 노드에는 아직 연결하지 않았다. 실제 서비스의 확정 계약이 아니다.
- `knowledge_cards/service.py`에는 조회용 `list_cards`가 있으나 카드 생성/저장 함수는 없다.
- 원본 파일은 Supabase Storage, 문서 메타는 `documents`, 카드는 `knowledge_cards`,
  카드와 원본 근거 연결은 `card_documents`에 두는 구조가 스키마에 명시되어 있다.
- 위 내용은 로컬 코드/SQL/문서 확인 결과다. 실제 원격 DB와 버킷 설정을 검증했다는 뜻은 아니다.
- DB 필드가 정해져 있는 것과 함수가 받을 입력·반환값이 정해져 있는 것은 다르다.
- `pyproject.toml`에 pdfplumber, python-docx, openpyxl, markdown-it-py를 추가했다.
  reportlab은 최소 PDF 테스트 문서를 만들기 위한 개발 의존성이다. 데이터셋 생성 기능은 없다.
  LangChain·LangGraph·OpenAI SDK는 아직 직접 의존성으로 등록하지 않았다.
  `app/config.py`에도 LLM/파싱 설정은 아직 없다. 기획 기술과 준비된 실행 환경을 구분한다.

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
이전 태그 객체형 내부 모델은 미커밋 단계이며 영속 데이터가 없다. 새 모델에 조용히 변환/폐기하지 않고 거부한다.
기존 공개 API/DB의 문자열 배열은 그대로 유지한다.
최종 카드의 title/summary 생성과 값 변환은 5번 draft_card 범위이며 아직 구현하지 않았다.

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

이번 모델의 검증 통과는 주장의 사실성을 입증하지 않는다. LLM 호출과 의미 검증은 아직 연결하지 않았다.
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
- TXT: UTF-8(BOM 포함) 후 CP949를 시도하며 문단·표시된 섹션·로그와 줄 순서를 보존한다.
- 표의 text는 셀을 탭/줄바꿈으로 표현한 추출 뷰다. 원래 셀 값과 좌표도 metadata에 보존한다.
  인용 검증은 이 표현 방식과 원본 위치를 고려해야 한다.

### 실패·청킹·재실행 정책

- 전체가 텍스트 없는 PDF는 TEXT_UNAVAILABLE로 실패한다. OCR/비전·이미지 설명은 아직 구현하지 않는다.
  혼합 PDF는 읽지 못한 페이지와 미전사 이미지 경고를 보존한다. 후속 Gate가 이를 무시해서는 안 된다.
- 빈 파일/콘텐츠, 잘못된 형식/인코딩, 손상 파일, 읽을 수 없는 경로는 사유를 구분한다.
- ParseOptions의 기본 로컬 제한은 파일 50MiB, Office 확장 데이터 200MiB, 시트 범위 200,000셀이다.
  호출자가 변경할 수 있으며, 외부 업로드 API의 합의된 제한이라는 뜻은 아니다.
- ChunkOptions.target_chars의 기본값은 6000자다. 토큰 한도가 아니라 조정 가능한 소프트 기준이다.
  표·코드·로그·문단 블록은 중간에서 자르지 않으며 첫 제목은 다음 블록과 함께 보관한다.
  기준을 넘는 블록은 oversized로 표시한다. 후속 Extract가 처리 기준을 정하기 전 자동 LLM 호출하지 않는다.
- Parse/Normalize/Chunk를 재실행하면 그 이후의 오래된 결과를 초기화한다.
  반면 Draft는 현재 extraction_result의 사실 상태와 근거를 유지해야 하며 5번에서 연결/검증한다.
- 현재 Parse 노드는 배치 중 하나가 실패하면 예외로 종료한다. 최종 작업의 부분 성공/SSE 정책은 8번에서 결정한다.
- 기존 API 경로·응답, DB·SQL은 변경하지 않았다. 업로드/Storage/백그라운드/SSE 및 전체 그래프는 후속 범위다.

로더 API 참고: [pdfplumber](https://github.com/jsvine/pdfplumber),
[python-docx](https://python-docx.readthedocs.io/en/latest/api/document.html),
[openpyxl](https://openpyxl.readthedocs.io/en/stable/api/openpyxl.reader.excel.html),
[markdown-it-py](https://markdown-it-py.readthedocs.io/en/latest/using.html).

## 어떤 입출력과 호출 계약이 미정인가

계약은 “누가 무엇을 넘기고, 무엇을 돌려주며, 실패는 어떻게 알리는가”에 대한 약속이다.
아래 예시 필드는 설명용이며 확정 필드가 아니다.

| 경계 | 이미 정해진 책임 | 아직 정할 구체적인 내용 |
|---|---|---|
| documents service → graph/parse | 상위 service가 업로드 후 파이프라인 실행 | 문서 ID·원래 파일명 외에 Storage 위치, bytes, 로컬 경로 중 무엇을 전달할지; 호출 함수명과 async 여부 |
| parse → normalize → chunk | DocumentIR와 원문 블록/근거 ID를 보존 | 기본 구조는 위와 같이 구현. OCR/비전, 복잡한 PDF 구조 및 청킹 고도화는 후속 |
| chunk → extract | 청크와 원문 ID로 구성 요소 추출 | 실제 LLM 호출·프롬프트·경험 구분, oversized 처리와 모델 토큰 한도 |
| extract → draft_card | 추출 요소를 카드 초안으로 구성 | 내부 추출 스키마는 위와 같이 구현. `CardDraft`와 최종 카드 변환 정책, 여러 경험을 구분/묶는 단위는 5번에서 결정 |
| verify → graph/save | pass/retry/reject/needs_review 판정, pass만 다음 단계 | `VerificationResult` 상세, 원인별 재처리·한도, 판정과 카드 상태 매핑, 검토 대기 보관 |
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
- MVP Verification Gate의 판정과 카드 상태 매핑, 검증 실패 시 저장 여부는 6번에서 결정한다.
  판정과 DB의 카드 상태는 다른 개념이다. 품질 점수 계산·가중치·사후 자동 개선·정기 재평가는 3순위다.
- quality_score/last_verified_at/verification_version은 기존 nullable 필드로 유지한다.
  실행하지 않은 3순위 평가 결과를 채우지 않는다. Gate 정보와의 관계는 후속 저장 계약에서 결정한다.
- 작업 진행 상태와 카드 상태도 서로 다른 개념이다. 카드 상태 컬럼을 작업 진행 저장소로 쓰지 않는다.
- needs_review는 Gate 판정이며 현재 CardStatus에 같은 값을 추가하지 않는다.
  검증을 통과한 active 카드와 구분한다. 검토 대기 결과를 어디에 보관할지는 6/8번에서 결정한다.
- 임베딩은 최대 4개이며 각 관점의 최소 생성 조건은 7번에서 별도로 정의한다.
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

## 네 가지 임베딩 — 구성 확정 / 구현 정책 미정

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

추가 제안: 원문을 보존하면서 임베딩 입력을 결정적으로 조립하고,
누락 정보를 LLM으로 보충하거나 가짜/0 벡터를 실제 임베딩처럼 저장하지 않는다.
모델과 템플릿 변경을 추적할 식별 정보는 저장 계약을 정할 때 함께 검토한다.

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
