# 프로젝트 개요

해커톤 과제: 프로젝트 산출물(설계서, 보고서, 이슈 기록 등)에 묻히는 기술 지식을
추출해 재사용 가능한 "지식카드"로 자산화하는 Agent.

MVP 범위 (지금 구현 대상):
- 사용자가 문서를 업로드하면 그 내용을 바탕으로 지식카드 생성
- 챗봇: 지식카드 기반 RAG 질문응답
- 지식카드 페이지: 카드 내용 열람 + 근거 원본 문서 확인

지식카드 품질 평가/사후관리는 MVP 이후 범위 (지금 설계에 억지로 끼워넣지 않는다).
지식카드/문서 테이블 구조는 `docs/schema.md` 참고.

# 아키텍처 규칙, 코드 컨벤션, DB 스키마

팀 공통 규칙(도메인 3분할 구조, 네이밍, 커밋/브랜치, DB 스키마 동기화)은
`.claude/skills/backend-conventions/SKILL.md` 하나에 정리되어 있다. 이 파일을 먼저 본다.
규칙이 바뀌어야 하면 그 스킬 파일을 팀 리뷰를 거쳐 수정한다 (여기 중복 작성하지 않음).

도메인(agent, users 등)별로 더 구체적인 규칙이 생기면 해당 도메인 폴더 안
`.claude/skills/`에 별도로 둔다.

# 로컬 실행 방법

1. 의존성 설치: `uv sync`
2. 환경변수 설정: `cp .env.example .env` 후 Supabase 값 채우기
3. 서버 실행: `uv run uvicorn app.main:app --reload`
4. 확인: http://localhost:8000/health

코드 포맷/import 순서는 ruff가 저장할 때 자동으로 정리한다. VSCode에서
`Ruff`(charliermarsh.ruff) 확장만 설치하면 되고, 수동으로 맞출 필요 없다.

# 브랜치/PR 규칙

- 브랜치는 `main` 하나만 기준으로 둔다 (develop/release 단계 없음).
- 모든 작업은 `main`에서 새 브랜치를 따서 시작한다: `git checkout main && git pull && git checkout -b <도메인명>/<작업내용>` (브랜치 이름 규칙은 backend-conventions 스킬 참고).
- `main`에 직접 push 금지. PR을 통해서만 머지한다.
- PR 리뷰 승인은 필요 없다. 셀프 머지 가능 (테크 리드는 실시간 승인자가 아니라 나중에 diff를 훑어보는 역할).
- 머지한 코드가 문제를 일으키면 `git revert <커밋해시>`로 되돌린다 (머지 커밋이면 `git revert -m 1 <커밋해시>`). 안 되면 팀장/인권님에게 바로 공유한다.
