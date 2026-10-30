# 노드: 재처리가 끝난 모든 경험의 최종 검증 결과를 모아 후속 단계에 전달한다.
# 이 노드에서는 임베딩 생성이나 저장을 수행하지 않는다.

from app.domains.documents.pipeline.execution import finish as finish_state


def finish(state):
    return finish_state(state)
