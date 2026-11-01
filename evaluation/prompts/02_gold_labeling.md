# 정답 사례 작성

사용 단계: 5~6단계. `00_common.md`를 앞에 붙인다.
문서마다 새 세션을 쓴다. 입력은 문서 원문뿐이다. 파이프라인 출력이나 다른 문서의 정답은 주지 않는다.

## 입력

- `document_key`
- 문서 원문 전체

## 과제

이 문서에서 지식카드가 될 수 있는 사례를 **빠짐없이** 찾고 필드별 정답을 작성한다.

## 사례의 기준

사례는 독립된 문제 해결 경험, 기술 결정, 또는 재사용할 수 있는 교훈이 있는 진단 하나다.

1. 같은 사건은 여러 절에 걸쳐 있어도 사례 1개다. 관점이나 절마다 나누지 않는다.
2. 번호가 붙은 별개 장애는 각각 사례다.
3. 측정표, 기술 나열, 개요, 계획만 있고 경험이 없으면 사례가 아니다.
4. `required`: 재사용할 지식으로 분명한 사례. 시스템이 놓치면 감점한다.
5. `optional`: 카드로 만들어도, 안 만들어도 타당한 사례.
6. 사례가 없으면 `cases`를 빈 배열로 두고 이유를 적는다.

## 필드 작성

필드는 context, problem, cause, approach, rationale, outcome, lesson이다.

1. `value`는 원문이 말한 내용을 짧게 쓴다. 해석을 보태지 않는다.
2. `status`는 공통 지침의 상태 중 하나다.
3. `quotes`에 근거 문장을 원문 그대로 넣는다. `unknown`이고 언급이 없으면 빈 배열이다.
4. 원문에 없는 필드는 `value: null`, `status: "unknown"`이다. 빈값이 정답일 수 있다.
5. 교훈은 `lesson_source`로 구분한다. `explicit`은 원문 명시, `derivable`은 조심스럽게 도출 가능,
   `none`은 도출 불가다.
6. 수행한 조치와 제안한 조치가 섞여 있으면 `approach`의 `value`는 수행한 조치만 쓰고,
   제안은 `proposed_actions`에 따로 쓴다.

## 금지 주장

원문이 명시적으로 미확정이거나 부정한 내용을 `forbidden_claims`에 적는다.
카드가 이것을 사실로 말하면 오류가 된다. 예: "재발이 사라졌다", "GPU가 원인이다".

## 출력 JSON

```json
{
  "document_key": "",
  "labeled_after_output": false,
  "no_case_reason": null,
  "cases": [
    {
      "case_id": "문서키#1",
      "title": "사례 이름",
      "tier": "required | optional",
      "split_notes": "사례를 이렇게 나눈 이유",
      "fields": {
        "context": {"value": null, "status": "unknown", "quotes": []},
        "problem": {"value": null, "status": "unknown", "quotes": []},
        "cause": {"value": null, "status": "unknown", "quotes": []},
        "approach": {"value": null, "status": "unknown", "quotes": []},
        "rationale": {"value": null, "status": "unknown", "quotes": []},
        "outcome": {"value": null, "status": "unknown", "quotes": []},
        "lesson": {"value": null, "status": "unknown", "quotes": []}
      },
      "lesson_source": "explicit | derivable | none",
      "proposed_actions": [{"value": "", "quotes": []}],
      "forbidden_claims": [{"claim": "", "reason": "", "quotes": []}]
    }
  ],
  "uncertain": [{"item": "", "reason": ""}]
}
```

작성 후 문서를 처음부터 다시 훑으며 빠진 사례가 없는지 확인한다.
