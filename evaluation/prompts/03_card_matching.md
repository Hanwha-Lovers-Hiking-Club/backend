# 카드와 정답 매칭

사용 단계: 11단계. `00_common.md`를 앞에 붙인다.
문서마다 새 세션을 쓴다. 게이트 판정 결과는 주지 않는다.

## 입력

- 문서 원문 전체
- 정답표 `v1`
- 파이프라인 카드 목록: `card_id`, 제목, 요약, 필드, 근거 인용문
- 스크립트가 만든 매칭 후보

## 과제

각 카드를 정답 사례에 매칭하고, 정답에 없는 카드는 사례 추가 여부를 판정한다.

## 매칭 절차

1. 같은 문서의 사례인지 확인한다.
2. 문제와 조치가 같은 사건을 가리키는지 확인한다.
3. 카드의 근거가 그 사례의 원문을 실제로 인용하는지 확인한다.

같은 배경 문장을 인용했다는 이유만으로 매칭하지 않는다.
다른 문장을 인용했어도 같은 사건이면 매칭한다.

## 결과 유형

| 유형 | 뜻 |
|---|---|
| `match` | 정답 사례에 대응하는 대표 카드. 정답 1건에 1장만 |
| `duplicate` | 이미 대표 카드가 있는 사례의 다른 카드 |
| `partial` | 한 사례의 일부만 다룬 카드. 대표 카드가 없으면 `match`로 쓰고 표시 |
| `merged` | 여러 정답 사례를 한 장에 합친 카드 |
| `unmatched` | 정답에 대응하는 사례가 없는 카드 |

한 사례에 카드가 여럿이면 사례를 가장 온전히 담은 카드를 `match`로 고르고 이유를 쓴다.

## 정답 보정

`unmatched` 카드마다 카드 내용에 기대지 말고 원문을 다시 읽어 판단한다.

- 원문에 독립 사례가 실제로 있고 정답표가 놓친 경우에만 `add_case`로 표시한다.
- 그렇지 않으면 `keep_false_positive`로 표시한다.
- `add_case`에는 `02_gold_labeling.md`의 사례 형식으로 필드를 작성한다.

## 출력 JSON

```json
{
  "document_key": "",
  "matches": [
    {
      "card_id": "",
      "result": "match | duplicate | partial | merged | unmatched",
      "case_ids": [],
      "reason": "",
      "quotes": []
    }
  ],
  "missed_case_ids": [],
  "gold_changes": [
    {
      "card_id": "",
      "decision": "add_case | keep_false_positive",
      "reason": "",
      "new_case": null
    }
  ],
  "uncertain": [{"item": "", "reason": ""}]
}
```
