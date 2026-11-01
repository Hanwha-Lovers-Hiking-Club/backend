# 주장 판정

사용 단계: 13단계. `00_common.md`를 앞에 붙인다.
입력은 최종 통과 카드에서 고정 시드로 뽑은 원자적 주장이다. 이전 판정 결과는 주지 않는다.

## 입력

- 주장 목록: `claim_id`, `document_key`, 필드, 주장 문장, 카드에 연결된 근거 인용문
- 해당 문서 원문 전체

## 과제

주장마다 카드에 연결된 근거가 그 주장을 뒷받침하는지 판정한다.
근거는 부족하지만 원문의 다른 곳에 내용이 있으면 따로 표시한다.

## 판정값

| 판정 | 뜻 |
|---|---|
| `supported` | 연결된 근거가 주장을 뒷받침하고 사실 상태도 같다 |
| `supported_elsewhere` | 주장은 원문과 맞지만 연결된 근거는 뒷받침하지 않는다 |
| `partially_supported` | 주장의 일부만 근거가 있다 |
| `unsupported` | 원문에 근거가 없다 |
| `contradicted` | 원문과 반대된다 |
| `uncertain` | 판단할 수 없다 |

`supported`만 지지로 집계한다.

## 상태 오류 표시

주장이 원문보다 강하게 단정하면 `error_type`을 `status_promotion`으로 표시한다.
문구는 정확한데 라벨만 어긋나면 `supported`로 판정하고 `status_label_only`로 표시한다.

예시:

- 원문 "A를 비교한다"(계획) → 주장 "A를 비교했다": `unsupported`, `status_promotion`
- 원문 "A가 원인일 수 있다" → 주장 "A가 원인이다": `partially_supported`, `status_promotion`
- 원문 "재발률은 측정하지 않았다" → 주장 "재발이 사라졌다": `contradicted`, `status_promotion`
- 원문 "해결 계획으로 A를 비교한다" → 주장 "해결 계획으로 A를 비교한다"(라벨 `confirmed`):
  `supported`, `status_label_only`

## 출력 JSON

```json
{
  "claims": [
    {
      "claim_id": "",
      "verdict": "supported | supported_elsewhere | partially_supported | unsupported | contradicted | uncertain",
      "source_status": "confirmed | proposed | hypothesis | unknown | conflicted",
      "error_type": "status_promotion | status_label_only | null",
      "reason": "",
      "quotes": []
    }
  ]
}
```
