# LangGraph 로컬 실행

이 도구는 검증·재처리와 7번 임베딩 입력 생성까지의 실제 LangGraph 실행을 확인한다.
실제 임베딩 API/DB 저장과 제품 백엔드의 업로드/SSE API는 아직 연결하지 않았다.
팀 연결 계약은 [regulations.md](../regulations.md)를 확인한다.

아래 환경 설정을 준비한 뒤 저장소 루트의 VS Code 터미널에서 실행한다.

```powershell
uv sync
uv run python scripts/start_langgraph.py
```

- 로컬 화면: http://127.0.0.1:2024/dev — 로그인 없이 그래프와 데모 실행 기록 확인.
- 개발 API: http://127.0.0.1:2024/docs
- Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
  Studio 사용에는 LangSmith 로그인이 필요할 수 있다. 로컬 화면은 계정 없이 사용할 수 있다.
- 백그라운드 로그/PID: `.local/langgraph/server.log`, `server-error.log`, `server.pid`.
  실행 상태와 `.langgraph_api` 체크포인트는 Git에서 제외한다. 개발 체크포인트는 제품 job DB가 아니다.
- 기본 포트는 2024다. `--port 2025`로 바꿀 수 있다.
- 스크립트는 서버를 숨김 창으로 시작하고 브라우저를 자동 조작하지 않는다. 이미 포트를 쓰는 프로세스는 중지하지 않는다.
  코드 변경을 반영하려면 이 서버 프로세스를 종료한 뒤 다시 시작한다.

## 환경 설정

설정은 `app/config.py`의 Settings가 읽는다. 비밀키는 Windows 사용자 환경변수로,
일반 설정은 저장소 루트의 로컬 `.env`로 제공한다. 같은 이름이면 프로세스 환경변수가 `.env`보다 우선한다.
`.env`는 Git에서 제외되며 새 clone에는 없으므로 직접 준비한다. 아래 예시의 프로젝트 URL은 실제 값으로 바꾼다.

```dotenv
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_STORAGE_BUCKET=documents
CORS_ORIGINS=http://localhost:3000

LLM_PROVIDER=openai
EXTRACT_MODEL=gpt-6.1-sol
DRAFT_MODEL=gpt-6-luna
VERIFY_MODEL=gpt-6.1-sol
VERIFY_RULES_ENABLED=true
VERIFY_LLM_ENABLED=false
VERIFY_MAX_RETRIES=2
DOCUMENT_LLM_TIMEOUT_SECONDS=60
DOCUMENT_LLM_MAX_OUTPUT_TOKENS=4096
DOCUMENT_LLM_MAX_INPUT_CHARS=48000
```

| 사용자 환경변수 이름 | 용도 |
|---|---|
| `OPENAI_API_KEY` | OpenAI Extract/Draft 및 선택적 LLM Verify |
| `SUPABASE_SERVICE_ROLE_KEY` | 제품 백엔드의 Supabase DB/Storage 접근 |
| `KURE_API_KEY` | 향후 KURE 임베딩 연결. 현재 제품 파이프라인에서는 미사용 |

환경변수의 값에는 발급받은 실제 키만 입력한다. 앞뒤 공백·줄바꿈·값을 감싸는 따옴표를 넣지 않는다.
`OPENAI_API_KEY="OPENAI_API_KEY"`는 변수 참조가 아니라 이름 문자열을 값으로 저장하는 설정이다.
키를 `.env`나 공용 문서에 다시 적지 않는다.

앱은 Windows 사용자 환경변수 저장소를 직접 조회하지 않고 시작한 프로세스의 환경변수를 읽는다.
등록·수정 후 VS Code를 완전히 종료하고 다시 열며, 이미 실행 중인 서버도 종료 후 다시 시작한다.
VS Code만 재시작해도 기존 서버의 환경변수는 바뀌지 않는다.

## 그래프 선택

- `documents_demo`: 실제 Parse/Normalize/Chunk/Extract/Draft/Verify 및 조건부 재처리, 임베딩 입력 텍스트 생성.
  LLM 부분은 명시적인 예제 응답이다. 원본은 이 폴더의 작은 demo.md이며 데이터셋 생성 기능이 아니다.
  첫 초안의 잘못된 주장을 검증에서 발견하고 Draft만 재작성하여 통과한다. API 비용/DB 저장은 없다.
- `documents`: 실제 `.env`의 공급자·단계별 모델을 사용한다. Studio 입력 예:

```json
{
  "sources": [{
    "document_id": "4c10a229-9b3a-40bc-8611-e2f787540f03",
    "original_filename": "example.md",
    "path": "C:/path/to/example.md"
  }]
}
```

document_id는 제품 연결 시 documents.id를 받아야 한다. 위 UUID는 로컬 확인용 예시다.
실제 파일은 서버에서 읽을 수 있어야 한다. 업로드/Storage 다운로드는 이 UI가 구현하지 않는다.

현재 기본 공급자는 OpenAI이며 Extract/Verify=gpt-6.1-sol, Draft=gpt-6-luna다.
설정 방법은 위 환경 설정 절을 따른다. KURE 호출은 아직 제품 그래프에 연결하지 않았다.
Ollama/Anthropic은 명시적으로 선택할 수 있으며 공급자 변경 시 모델 ID도 바꿔야 한다.
Ollama qwen 모델은 자동 다운로드하지 않으며 유료 API로 자동 fallback하지 않는다.
규칙 모드에서는 의미를 확인할 수 없는 카드는 needs_review다. LLM 의미 검증을 쓰려면
VERIFY_LLM_ENABLED=true와 VERIFY_MODEL을 명시한다. 예제의 통과가 실제 모델의 품질을 입증하지는 않는다.

## 최소 연결 확인 기록 — 2026-10-30

사용자 요청으로 실제 키를 사용해 다음 최소 요청만 확인했다. 정기 자동 테스트가 아니다.

| 서비스 | 확인한 요청 | 결과 |
|---|---|---|
| OpenAI | Sol·Luna 각각 짧은 요청 1회. 기존 OpenAI 어댑터 사용 | 두 모델 모두 구조화 JSON 응답 검증 통과 |
| Supabase | 기존 앱 클라이언트로 knowledge_cards의 ID 최대 1건 조회 | 정상 응답, 결과 0건. DB 쓰기 없음 |
| KURE | kure-v3에 짧은 문장 1개 임베딩 | HTTP 200, 기본 2,048차원 벡터, 입력 6토큰 |

KURE는 등록된 키 앞뒤 공백 때문에 첫 시도에서 로컬 헤더 검증에 실패했다.
이번 요청에서만 공백을 제거해 성공했으며 사용자 환경변수 원본은 수정하지 않았다.
다음 실행 전 등록된 값의 공백을 제거하고 해당 프로세스를 재시작해야 한다.

이 결과는 해당 시점의 인증과 최소 호출 성공만 확인한다. 실제 프로젝트 문서의 추출·검증 품질,
작업당 비용, 지속 처리량·할당량, Supabase Storage 업로드·카드 저장은 아직 검증하지 않았다.
KURE의 관측된 기본 차원은 최종 임베딩/DB/검색 계약을 확정한 값이 아니다.
실제 파이프라인 전체 실행과 구분하며, LLM Verify의 기본 비활성 설정도 유지한다.

## 중단·재개와 결과

Python에서 `build_graph(checkpointer=..., interrupt_before=["verify"])`로 검증 직전 중단을 사용할 수 있다.
동일 thread_id에서 `ainvoke(None, config)`로 재개한다. 입력을 다시 보내 새 실행을 시작하는 방식과 구분한다.
Agent Server에서 실행할 때는 서버가 체크포인트를 관리하므로 별도 checkpointer를 그래프에 넣지 않는다.

통과 초안은 verified_drafts, 보류 초안은 review_drafts, 판정은 verification_results에서 확인한다.
`finish` 다음 `build_embedding_texts`가 실행되며 State의 embedding_texts에서 경험별 texts/skipped를 확인한다.
관점별 최소 생성 조건과 `card-views-v1` 템플릿은 memo.md를 따른다. 실제 벡터나 저장 결과는 아니다.
선택적 입력 `embedding_text_options: {"max_chars": 12000}`으로 관점별 글자 수를 제한할 수 있다.
기본값은 제한 없음이며, 초과 관점은 자르지 않고 보류한다. 실제 모델 토큰 한도는 Embed에서 별도 검사해야 한다.
settled_*와 all_*는 재처리 중 다른 경험의 결과·원본을 잃지 않기 위한 작업 내부 상태다.
파싱 예외/모델 호출 오류는 그래프 오류로 전파한다. 저장 성공이나 completed job으로 대신 표시하지 않는다.
최종 제품 job 상태 및 API/SSE 매핑은 regulations.md의 C03 계약에서 합의한다.

공식 참고: [로컬 서버](https://docs.langchain.com/oss/python/langgraph/local-server),
[Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
[개발용 HTTP 앱](https://docs.langchain.com/langsmith/custom-routes).
