# 노드: 범위를 한정한 재추출 후 이미 처리됐거나 재처리 대기 중인 경험을 제외하고 새 후보를 선택한다.

from app.domains.documents.pipeline.execution import select_candidates as select_state


def select_candidates(state):
    return select_state(state)
