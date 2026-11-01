# 게이트 전 초안 판정

사용 단계: 12단계. `00_common.md`를 앞에 붙인다.
입력은 `first_drafts.json`의 초안이다. 게이트 판정과 재시도 여부는 주지 않는다.

## 입력

- 문서 원문 전체
- 초안 목록: 제목, 요약, 교훈, 필드값, 필드 상태, 필드별 근거 ID와 인용문

## 과제

초안마다 원문 기준으로 `good` 또는 `bad`를 판정한다.
게이트가 이 초안을 어떻게 처리했을지는 추측하지 않는다.

## 판정 기준

아래 오류가 하나라도 있으면 `bad`다.

| 오류 유형 | 뜻 |
|---|---|
| `unsupported_claim` | 원문에 근거가 없는 주장 |
| `status_promotion` | 미확인, 가설, 제안을 확정이나 완료처럼 서술 |
| `contradiction` | 원문과 반대되는 서술, 부정 표현 누락 |
| `altered_value` | 수치, 날짜, 버전, 이름을 바꿈 |
| `evidence_mismatch` | 연결된 근거가 해당 주장을 뒷받침하지 않음 |
| `merged_cases` | 서로 다른 사건을 하나의 인과관계로 묶음 |
| `forbidden_claim` | 원문이 미확정이나 부정이라고 한 내용을 사실로 말함 |

아래는 `bad`로 판정하지 않고 `notes`에 기록한다.

| 표시 | 뜻 |
|---|---|
| `status_label_only` | 문구에 "계획", "제안"이 남아 있지만 상태가 `confirmed` |
| `duplicate_like` | 같은 문서의 다른 초안과 내용이 겹침 |
| `low_value` | 사실은 맞지만 재사용 가치가 낮음 |
| `field_misplaced` | 사실은 맞지만 다른 필드에 들어갈 내용 |

## 확인 순서

1. 제목과 요약의 주장을 나누어 원문과 대조한다.
2. cause, approach, outcome의 값과 상태를 원문의 시제와 단정 정도로 대조한다.
3. 교훈이 원문에 있거나 원문에서 도출 가능한지 확인한다. 과도한 일반화면 `unsupported_claim`이다.
4. 연결된 근거가 해당 필드를 실제로 뒷받침하는지 확인한다.

## 출력 JSON

```json
{
  "document_key": "",
  "drafts": [
    {
      "experience_id": "",
      "verdict": "good | bad | uncertain",
      "errors": [
        {"type": "", "field": "", "claim": "", "reason": "", "quotes": []}
      ],
      "notes": [{"type": "", "field": "", "reason": ""}]
    }
  ]
}
```
