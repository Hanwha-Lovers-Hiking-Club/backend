# 지식카드 저장 연결 계약

2026-11-02 HYUNGU 구현 기준. 현재 코드의 연결 계약이며 팀 전체의 승인이나 운영 DB 적용을 뜻하지 않는다.
원격 `origin/main`의 `27b2198`에서 확인한 단건 저장 함수와 함께 비교한다.

## 확인한 팀 구현과 연결 판단

`origin/main:app/domains/knowledge_cards/service.py`에는 다음 동기 함수가 있다.

```python
def create_card(card: dict, evidences: list[dict]) -> str: ...
```

카드를 insert한 뒤 근거를 별도 insert하고 카드 ID를 반환한다. 일반 dict로 두 도메인의 내부
Pydantic 타입을 분리한다는 의도는 유지한다. 다만 두 요청의 원자성·영속 멱등 키·문서 0건 완료는
구현되어 있지 않다. 근거 오류나 첫 응답 유실 후 재시도에 취약하므로 현재 파이프라인에서 이 함수를
카드 수만큼 반복 호출하지 않는다. main을 병합하거나 이 함수를 HYUNGU에 복제한 것이 아니다.

현재 연결은 아래와 같다.

```text
pipeline save 노드
 → pipeline.persistence.save_document(DocumentSaveRequest)
 → build_save_payload: 내부 모델을 일반 dict로 변환
 → knowledge_cards.service.save_document_cards(dict) → dict
 → save_document_cards RPC 1회
 → 파이프라인 SaveReceipt로 변환·대조
```

`get_graph()`는 이 어댑터를 연결한다. `build_graph(document_saver=...)`의 테스트/개발용 주입은
기존 내부 계약을 유지한다. fake_save_cards는 연결하지 않는다.

## 공개 서비스 요청

정의 파일은 `contracts.py`다. 서비스 경계에서는 JSON으로 직렬화 가능한 일반 dict만 전달한다.
파이프라인의 DocumentIR·KnowledgeCardDraft·경험 ID·클라이언트를 전달하지 않는다.

| 필드 | 형태 | 책임/제약 |
|---|---|---|
| document_id | UUID 문자열 | 업로드 담당이 만든 기존 documents.id. 임의 신규 문서 생성 금지 |
| payload_hash | 소문자 16진수 64자리 | 현재 문서의 통과 카드·벡터 집합 해시. 인증값이 아님 |
| cards | 카드 dict 배열, 0개 허용 | Gate pass 카드만 어댑터가 전달 |

카드 dict는 기존 DB 필드명 title, summary, context, problem, cause, approach, rationale,
outcome, lesson, technologies, keywords를 사용한다. title/summary/context/lesson은 공백이 아닌
문자열이고, 조건부 다섯 필드 중 최소 하나는 있어야 한다. 기술/키워드는 문자열 배열이다.
status는 이 파이프라인 전용 계약에서 active만 허용한다. 서비스 자체는 Gate를 다시 실행하지 않으며
pass 목록 선택은 호출하는 파이프라인의 책임이다.

| 추가 필드 | 형태 | 제약 |
|---|---|---|
| evidences | 근거 dict 배열, 최소 1개 | 각 항목은 document_id, location, excerpt. 현재 한 문서의 근거만 허용 |
| content_embedding | 숫자 배열 또는 null | 생성 가능한 관점만 저장. 현재 설정 1024차원 |
| situation_embedding | 숫자 배열 또는 null | 동일 |
| solution_embedding | 숫자 배열 또는 null | 동일 |
| lesson_embedding | 숫자 배열 또는 null | 동일 |

NaN/무한대/불리언/0 벡터와 차원 오류는 DB 요청 전에 거부한다. 누락 관점은 null로 전달하며
가짜 벡터로 대체하지 않는다. 모델·입력 해시 검사는 어댑터에서 유지한다. location/excerpt의
공백·줄바꿈은 변경하지 않는다. 서비스는 document_id를 대조한 뒤 RPC의 문서 ID를 통해 근거를 저장한다.
quality_score/last_verified_at/verification_version을 생성하거나 요청에 넣지 않는다.
현재 RPC는 프로젝트·부서 메타데이터와 필드별 사실 상태/근거 링크를 중앙 DB에 저장하지 않는다.

## 반환과 오류

```python
{
    "document_id": "<요청과 같은 UUID>",
    "payload_hash": "<요청과 같은 해시>",
    "committed": True,
    "result_recorded": True,
    "card_ids": ["<실제 저장된 UUID>"],
}
```

card_ids는 요청한 pass 카드 수와 같고 중복이 없어야 한다. 0건이면 빈 배열이다.
result_recorded는 pass 카드 집합의 저장 영수증이며, Job 전체 완료나 needs_review 카드의 저장을
뜻하지 않는다. 서비스는 계약·DB 오류에 CardPersistenceError를 발생시키며 어댑터는 이를
DocumentSaveError로 변환한다. Save 노드는 SAVE_NOT_CONFIRMED로 기록하고 임시 근거를 유지한다.
오류 후 fake 성공이나 임의 ID를 반환하지 않는다. 서비스 내부 자동 저장 재시도는 추가하지 않았다.

## 원자성·중복 방지·정리

- RPC SQL은 한 문서의 카드·근거·벡터·영수증을 한 트랜잭션에 저장하도록 작성되어 있다.
  여러 서비스 함수를 Python에서 순서대로 호출하는 것만으로 이 보장이 생기지는 않는다.
- 같은 document_id + payload_hash는 기존 card_ids를 반환한다. 같은 문서의 다른 해시는 거절한다.
  experience_id나 카드 제목을 영속 멱등 키로 사용하지 않는다.
- pass와 needs_review가 섞이면 pass만 저장하고 문서는 needs_review + saved_card_ids로 남는다.
  보류 근거와 SQLite 체크포인트는 정리하지 않는다.
- 보류가 없고 저장이 확인된 문서만 completed로 전환해 현재 임시 상태를 지운다.
  전체 Job 체크포인트 정리는 모든 문서 완료와 최종 Job 결과 DB 기록 이후다.
- 보류 카드 추가 승인 후 같은 문서에 새 카드를 더 저장하는 정책은 아직 지원하지 않는다(C05).

## 조회 호환성

list_cards와 CARD_SELECT/_to_card는 현재 구현을 유지한다. 목록은 active 카드만 조회하고,
card_documents와 documents를 통해 근거·원래 파일명을 반환한다. 외부 API 경로나 응답은 변경하지 않는다.

## 다음 팀 확인과 검증

| 담당 | 확인할 내용 |
|---|---|
| knowledge_cards 담당 | 현재 문서 배치 함수를 공용 진입점으로 유지할지, create_card와 어떻게 공존할지 |
| knowledge_cards/DB 담당 | 단건 함수로 통합하려면 카드·근거·벡터 원자성과 영속 멱등성, 0건 기록을 어떻게 제공할지 |
| DB 담당/팀장 | 기존 migration과 저장 RPC의 실제 적용 여부 및 적용 실행 |
| pipeline/카드/프런트 담당 | 보류 승인·추가 저장 정책, C04/C05/C06 중앙 근거 보관 |

로컬 테스트는 실제 그래프→어댑터→서비스→모의 RPC와 오류 보존을 확인한다. PostgreSQL의 실제
롤백·동시 멱등성은 모의 테스트로 입증하지 않는다. 운영 DB 적용 상태는 이번에 확인하지 않았다.
SQL 적용 후 staging에서 `scripts/smoke_document_save.py`로 실제 저장·재호출·롤백·0건을 확인한다.
실제 DB SQL 실행은 팀장 담당이며 이번 연결 작업에서는 실행하지 않는다.
