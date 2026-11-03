# 기본 RAG 인덱싱 파이프라인 전체 연결: parse -> chunk -> contextualize -> embed -> save.
# 지식카드 단계(extract, draft_card, verify)는 없다.
#
# 에러 분기: 어느 노드든 state["error"]를 채우면 남은 노드를 건너뛰고 바로 끝낸다.
# main의 graph와 같은 방식이다.
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domains.baseline_documents.pipeline.nodes.chunk import chunk
from app.domains.baseline_documents.pipeline.nodes.contextualize import contextualize
from app.domains.baseline_documents.pipeline.nodes.embed import embed
from app.domains.baseline_documents.pipeline.nodes.parse import parse
from app.domains.baseline_documents.pipeline.nodes.save import save
from app.domains.baseline_documents.pipeline.state import IngestionState

_NODES = {
    "parse": parse,
    "chunk": chunk,
    "contextualize": contextualize,
    "embed": embed,
    "save": save,
}
_NODE_ORDER = list(_NODES)


def _route_after(node: str):
    is_last = node == _NODE_ORDER[-1]
    next_node = END if is_last else _NODE_ORDER[_NODE_ORDER.index(node) + 1]

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
