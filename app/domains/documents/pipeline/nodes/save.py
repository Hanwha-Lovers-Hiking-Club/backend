# 노드: 검증을 통과한 카드를 저장한다.
# knowledge_cards 도메인의 service를 호출하며, 테이블에 직접 쓰지 않는다.
# 실제 서비스 어댑터가 없으면 저장 확인을 만들지 않고 결과를 보관한다.

from app.domains.documents.pipeline.document_lifecycle import make_document_saver


def make_save_node(saver=None):
    return make_document_saver(saver)
