# 문서 선정 확정

사용 단계: 4단계. `00_common.md`를 앞에 붙인다.
입력으로 후보 문서의 전문과 제안 목록을 준다. 파이프라인 출력은 주지 않는다.

## 입력

- 프로젝트별 후보 문서 전문
- 3단계 제안 목록: 문서 키, 유형, 제안 이유

## 과제

평가에 쓸 문서 18개를 확정하고 조정용 6개, 평가용 12개로 나눈다.

## 규칙

1. 프로젝트 6개에서 3개씩 고른다.
2. 프로젝트별로 사례·ADR·측정 기록 중 2개와 카드 생성 여부가 애매한 문서 1개를 고른다.
3. 조정용은 프로젝트당 1개, 평가용은 프로젝트당 2개다.
4. `ssafesta/webgl-stutter-diagnosis-2026-09-07`은 조정용으로 고정한다.
5. 같은 사건을 다룬 문서는 한쪽에만 둔다. 먼저 사건 묶음을 찾는다.
6. 사례 수를 전부 표시할 수 없을 만큼 큰 문서는 제외한다.
7. 음성 후보는 문서 종류로 정하지 않는다. 카드로 만들 결정이나 교훈이 있는지 읽고 판단한다.
8. 해결 완료, 해결 미확인, 원인 미확정, 제안만 있는 문서가 평가용에 고르게 들어가게 한다.

## 출력 JSON

```json
{
  "documents": [
    {
      "document_key": "프로젝트/파일 식별자",
      "path_under_original_markdown": "상대 경로",
      "split": "tuning | evaluation",
      "type": "case | adr | measurement | ambiguous",
      "expected_cardable": "yes | no | maybe",
      "estimated_cases": 0,
      "event_group": "같은 사건 묶음 이름 또는 null",
      "resolution_state": "resolved | unverified | cause_unconfirmed | proposal_only | none",
      "reason": "선정 이유. 음성이면 근거가 부족한 이유"
    }
  ],
  "excluded": [{"document_key": "", "reason": ""}],
  "balance_notes": "조건을 못 맞춘 부분"
}
```
