# 노드: 다음 재처리 단계에 필요한 범위의 원본과 중간 결과를 복원한다.

from app.domains.documents.pipeline.execution import prepare_retry as prepare_state


def prepare_retry(state):
    return prepare_state(state)
