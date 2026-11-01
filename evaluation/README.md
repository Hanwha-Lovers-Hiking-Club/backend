# 지식카드 생성 평가

지식카드 생성 파이프라인의 성능을 재는 오프라인 평가 도구와 지침을 둔다.
규칙은 [guidelines.md](guidelines.md), AI 판정 프롬프트는 [prompts/](prompts/)를 따른다.

## 구성

| 경로 | 내용 | Git |
|---|---|---|
| `guidelines.md` | 평가 범위, 채점 규칙, 지표, 작업 순서 | 포함 |
| `prompts/` | AI 판정자에게 주는 프롬프트 | 포함 |
| `run_document.py` | 문서 1개를 저장 없이 처리하는 실행기 | 포함 |
| `runs/` | 실행 결과. 원문 포함 | 제외 |
| `labels/` | 정답표. 원문 포함 | 제외 |
| `judgments/` | 판정 결과. 원문 포함 | 제외 |

저장소가 공개 상태이고 일부 데이터셋은 라이선스가 없으므로 제외 항목은 커밋하지 않는다.

## 문서 실행

`OPENAI_API_KEY`와 앱 시작에 필요한 환경변수를 설정한 뒤 저장소 루트에서 실행한다.

```bash
uv run python -m evaluation.run_document \
  --document "<문서 경로>" \
  --document-key "<프로젝트/문서 이름>" \
  --output evaluation/runs/<회차>/<문서 이름>
```

실제 LLM을 호출하므로 비용이 든다. 임베딩은 건너뛰고 저장 어댑터를 연결하지 않으므로
KURE와 Supabase는 호출하지 않는다. 결과 폴더가 이미 있으면 덮어쓰지 않고 멈춘다.

## 실행 결과

| 파일 | 내용 |
|---|---|
| `summary.json` | 커밋, 모델, 프롬프트 해시, 토큰, 시간, 판정, DB 모듈 로드 여부 |
| `first_drafts.json` | 재시도 전 첫 초안. 판정자에게 주는 입력이며 게이트 결과가 없다 |
| `final_state.json` | 최종 State. `verified_drafts`, `review_drafts`, `verification_results` 포함 |
| `events.jsonl` | 노드별 update 전체 |
| `calls.json` | LLM 호출별 단계, 모델, 시간, 토큰 |

## 시험 실행 기록 (2026-11-01)

저장 없는 실행이 가능한지 확인하려고 문서 1개를 처리했다. 성능 평가 결과가 아니다.

| 확인 항목 | 결과 |
|---|---|
| 게이트 전후 초안 보존 | 첫 초안과 최종 State 모두 저장됨. 재시도가 없어 덮어쓰기는 미확인 |
| 임베딩 우회 | KURE 호출 0회 |
| DB 미사용 | Supabase 모듈 미로드, 문서 상태 `ready_to_save` |
| 처리량 | 7청크, LLM 17회, 139초, 입력 55,741토큰, 출력 13,739토큰 |
