# 기본 RAG 챗봇 검색 그래프 연결: rewrite -> retrieve -> rerank -> expand.
# generate는 토큰 스트리밍 때문에 그래프에 넣지 않고 service.py가 그래프 실행 후 직접 호출한다.
#
# 노드에서 예외가 나면 그래프 실행이 그대로 실패한다.
# service.py가 그 예외를 잡아 error 이벤트로 알린다.
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domains.baseline_chat.pipeline.nodes.expand import expand
from app.domains.baseline_chat.pipeline.nodes.rerank import rerank
from app.domains.baseline_chat.pipeline.nodes.retrieve import retrieve
from app.domains.baseline_chat.pipeline.nodes.rewrite import rewrite
from app.domains.baseline_chat.pipeline.state import RetrievalState

_NODES = {
    "rewrite": rewrite,
    "retrieve": retrieve,
    "rerank": rerank,
    "expand": expand,
}


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(RetrievalState)
    for name, node in _NODES.items():
        graph.add_node(name, node)

    order = list(_NODES)
    graph.add_edge(START, order[0])
    for current, following in zip(order, order[1:]):
        graph.add_edge(current, following)
    graph.add_edge(order[-1], END)

    return graph.compile()
