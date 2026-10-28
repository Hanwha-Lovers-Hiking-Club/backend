# 파이프라인 전체 연결: parse -> chunk -> extract -> draft_card -> verify -> embed -> save.
#
# ponytail: verify에서 rewrite 판정이 나와도 draft_card로 돌아가는 재시도 루프는 안 만든다.
# rewrite는 지금 discard와 동일하게 "저장 안 함"으로 취급한다(embed/save가 verdict == "pass"만
# 거르므로 자연히 그렇게 됨). 이유: 통과한 카드까지 같이 재생성되는 문제, 리스트 길이가 안 맞을 수
# 있는 문제(draft_card가 source_chunk_ids 못 찾으면 skip) 때문에 "rewrite 난 것만 선택적으로
# 재시도"하려면 state 구조를 더 바꿔야 해서, 지금은 범위 밖으로 미룬다(카드 품질 사후관리는
# CLAUDE.md 기준 MVP 이후 범위). 업그레이드 경로: CardDraft에 원본 ExtractedElements 역추적용
# 필드를 추가하고, card_drafts/verified_cards를 라운드마다 누적하는 reducer로 바꾸고, graph에
# draft_card로 돌아가는 조건부 엣지 + 재시도 횟수 제한을 추가한다.
#
# 에러 분기: 어느 노드든 state["error"]를 설정하면, 남은 노드를 건너뛰고 즉시 끝낸다.
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domains.documents.pipeline.nodes.chunk import chunk
from app.domains.documents.pipeline.nodes.draft_card import draft_card
from app.domains.documents.pipeline.nodes.embed import embed
from app.domains.documents.pipeline.nodes.extract import extract
from app.domains.documents.pipeline.nodes.parse import parse
from app.domains.documents.pipeline.nodes.save import save
from app.domains.documents.pipeline.nodes.verify import verify
from app.domains.documents.pipeline.state import IngestionState

_NODE_ORDER = ["parse", "chunk", "extract", "draft_card", "verify", "embed", "save"]
_NODES = {
    "parse": parse,
    "chunk": chunk,
    "extract": extract,
    "draft_card": draft_card,
    "verify": verify,
    "embed": embed,
    "save": save,
}


def _route_after(node: str):
    next_node = END if node == _NODE_ORDER[-1] else _NODE_ORDER[_NODE_ORDER.index(node) + 1]

    def router(state: IngestionState) -> str:
        return END if state.get("error") else next_node

    return router


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(IngestionState)
    for name, node in _NODES.items():
        graph.add_node(name, node)

    graph.add_edge(START, _NODE_ORDER[0])
    for node in _NODE_ORDER:
        graph.add_conditional_edges(node, _route_after(node))

    return graph.compile()
