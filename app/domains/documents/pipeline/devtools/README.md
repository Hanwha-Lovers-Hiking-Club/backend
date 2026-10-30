# LangGraph 로컬 실행

이 도구는 6번까지의 실제 LangGraph 실행을 확인한다. 제품 백엔드의 업로드/SSE API와 별개다.
팀 연결 계약은 [regulations.md](../regulations.md)를 확인한다.

저장소 루트의 VS Code 터미널에서 실행한다.

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

## 그래프 선택

- `documents_demo`: 실제 Parse/Normalize/Chunk/Extract/Draft/Verify 및 조건부 재처리.
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

현재 `.env`의 qwen3:14b/qwen3:8b는 확인 당시 설치되지 않았다. 실제 그래프 실행 전에 설치하거나
사용할 설치 모델로 EXTRACT_MODEL/DRAFT_MODEL을 직접 변경해야 한다. 자동 다운로드/유료 fallback은 없다.
규칙 모드에서는 의미를 확인할 수 없는 카드는 needs_review다. LLM 의미 검증을 쓰려면
VERIFY_LLM_ENABLED=true와 VERIFY_MODEL을 명시한다. 예제의 통과가 실제 모델의 품질을 입증하지는 않는다.

## 중단·재개와 결과

Python에서 `build_graph(checkpointer=..., interrupt_before=["verify"])`로 검증 직전 중단을 사용할 수 있다.
동일 thread_id에서 `ainvoke(None, config)`로 재개한다. 입력을 다시 보내 새 실행을 시작하는 방식과 구분한다.
Agent Server에서 실행할 때는 서버가 체크포인트를 관리하므로 별도 checkpointer를 그래프에 넣지 않는다.

통과 초안은 verified_drafts, 보류 초안은 review_drafts, 판정은 verification_results에서 확인한다.
settled_*와 all_*는 재처리 중 다른 경험의 결과·원본을 잃지 않기 위한 작업 내부 상태다.
파싱 예외/모델 호출 오류는 그래프 오류로 전파한다. 저장 성공이나 completed job으로 대신 표시하지 않는다.
최종 제품 job 상태 및 API/SSE 매핑은 regulations.md의 C03 계약에서 합의한다.

공식 참고: [로컬 서버](https://docs.langchain.com/oss/python/langgraph/local-server),
[Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
[개발용 HTTP 앱](https://docs.langchain.com/langsmith/custom-routes).
