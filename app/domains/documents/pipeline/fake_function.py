# 카드 저장 서비스가 준비되기 전 사용하는 개발용 가짜 함수다.
# DB/Storage에 접근하지 않으며 향후 knowledge_cards.service의 실제 저장 함수로 교체한다.
# 입력 필드, 근거 전달 방식, 실제 반환값과 원자성은 아직 합의 전이다.
# 현재 임시 입력·반환 타입을 도메인 공통 계약으로 사용하지 않는다.

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class FakeSaveResult:
    """가짜 실행 결과. 받은 카드 수와 실제 DB 생성 수를 구분한다."""

    received_card_count: int
    is_fake: Literal[True] = field(default=True, init=False)
    created_card_count: Literal[0] = field(default=0, init=False)
    saved_card_ids: tuple[str, ...] = field(default=(), init=False)


async def fake_save_cards(cards: Sequence[Mapping[str, object]]) -> FakeSaveResult:
    """카드 payload를 변경하지 않고 수신 건수만 돌려준다. 실제 저장은 하지 않는다.

    입력 필드는 해석/검증하지 않는다. 가짜 성공 ID나 실제 저장 건수를 만들지 않는다.
    graph/save 연결은 IngestionState와 실제 저장 함수 계약이 정해진 뒤 수행한다.
    """
    return FakeSaveResult(received_card_count=len(cards))
