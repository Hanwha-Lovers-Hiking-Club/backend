# 노드: 그래프 입력을 데이터 모델로 변환하고 새 작업의 실행 상태를 초기화한다.

from app.domains.documents.pipeline.execution import initialize as initialize_state


def initialize(state):
    return initialize_state(state)
