# 백엔드 API 체크리스트 (중간 점검)

기준: 선임 요구사항 "엔드포인트 4개 선구현" + `API 설계서.pdf`
번호 = 진행 순서. [x] 완료 / [~] 일부 완료 / [ ] 남음

## 완료한 일

- [x] 1. 공통 기반 (PR #2 `core/setup-api-foundation`)
  - CORS 미들웨어(`settings.CORS_ORIGINS`), 도메인 API는 `/api` prefix, `/health`
  - pytest + httpx 테스트 환경
- [x] 2. API-004 지식카드 목록 `GET /api/knowledge/cards` (PR #3)
  - Supabase 실제 연동, active 카드만 반환, camelCase 응답 + evidences[]
  - q(제목/요약/배경 부분일치, 키워드 정확일치), sort(recent/created/name), page/pageSize
  - 페이지 범위를 넘으면 빈 목록을 돌려주고, DB 오류는 500으로 처리
  - evidences[].id는 설계서의 복합키 대신 card_documents.id를 사용, url은 null
- [x] 3. API-002 문서 업로드 `POST /api/documents/upload` (PR #5)
  - pdf/docx/md만 허용(400), 파일당 50MB 이하(413), 하나라도 실패하면 접수하지 않음
  - Storage 저장 → documents 테이블에 한 번에 insert, 실패하면 올린 파일 삭제
  - 202 + {jobId, documentCount, status: "queued"}, 작업 상태는 서버 메모리(`_jobs`)에 둠
- [~] 4. API-003 작업 상태 SSE `GET /api/documents/jobs/{job_id}/events` (PR #6)
  - [x] progress / completed / failed 이벤트, 재연결 시 최신 상태부터 전송, 없는 작업은 404
  - [ ] 진행 이벤트 Mock 발행: 지금은 업로드 후 상태를 바꾸는 코드가 없어서 step 0에 멈춤 (→ 6번)

## 앞으로 할 일

- [x] 5. API-001 챗봇 SSE Mock `POST /api/chat/stream` (새 `chat` 도메인, 브랜치 `chat/add-stream-mock`)
  - `app/domains/chat/` router/schemas/service 3분할로 만들고 main.py에 등록 (prefix `/chat` → 설계서 경로와 같음)
  - 요청 {message, history[]}, 이벤트 token → references → completed, 실패하면 error
  - Mock 답변은 함수 하나로 분리하고 응답에 Mock 표시 (나중에 RAG로 교체)
  - 기존 `agent` 도메인(스캐폴딩 예시 `POST /api/agent/ask`)은 건드리지 않고 유지
    - RAG 로직을 agent에 둘지 chat에 둘지 RAG 담당자와 정하기
- [ ] 6. 작업 진행 이벤트 Mock 발행 (브랜치 `documents/add-mock-job-runner`)
  - 업로드 후 백그라운드 작업으로 activeStep 0→5 진행 → completed (Mock 카드는 DB에 저장 안 함)
  - 팀원 파이프라인(`origin/document/pipeline`) graph가 완성되면 실제 실행으로 교체
    - graph가 먼저 머지되면 이 단계는 건너뛰고 바로 실제 연결
- [ ] 7. 원본 문서 열람 URL `GET /api/documents/{id}/url` (설계서 3순위)
  - Storage 서명 URL 발급 → 지식카드 evidences[].url 채우기
- [ ] 8. 지식카드 상세 `GET /api/knowledge-cards/{id}` (설계서 3순위)
  - 목록 경로(`/api/knowledge/cards`)와 맞춰 통일

## 공유하거나 확인할 것

- [ ] 경로가 설계서와 다름: 업로드 `/api/documents/upload`, SSE `/api/documents/jobs/{id}/events`
  - 프론트 `connectJob`과 설계서 수정을 공유했는지 확인
- [ ] 지원 파일 형식: 팀원 브랜치에서 txt/xlsx를 추가함 (머지되면 반영)
- [ ] 작업 상태 저장소(메모리 → 테이블 등)와 실패 시 문서·카드 처리 정책은 미확정
