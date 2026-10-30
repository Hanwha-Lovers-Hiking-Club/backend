# 노드: 성공한 청크들의 독립 경험을 중복 없이 합친다. 실패 기록은 보존한다.

from app.domains.documents.pipeline.chunk_execution import merge_extraction as merge


def merge_extraction(state):
    return merge(state)
