# 노드: 청크별 실행 기록과 이번 추출 대상 목록을 준비한다.

from app.domains.documents.pipeline.chunk_execution import prepare_extraction as prepare


def prepare_extraction(state):
    return prepare(state)
