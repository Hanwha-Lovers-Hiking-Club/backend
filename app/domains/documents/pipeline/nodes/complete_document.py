# 노드: 문서의 완료·실패를 기록하고 실제 저장 확인 후 그 문서의 임시 데이터를 정리한다.

from app.domains.documents.pipeline.document_lifecycle import make_document_completer


async def complete_document(state):
    return await make_document_completer()(state)
