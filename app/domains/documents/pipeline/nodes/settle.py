# 노드: 경험별 검증 결과를 보존하고 Gate에서 횟수를 제한한 재처리 요청을 대기열에 넣는다.

from app.domains.documents.pipeline.execution import settle as settle_state


def settle(state):
    return settle_state(state)
