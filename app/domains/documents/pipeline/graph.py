# 파이프라인 전체 연결:
# parse -> chunk -> extract -> draft_card -> embed -> save -> embed_chunks -> save_chunks.
# draft_card 다음의 검증 노드는 아직 없다. 검증 안 된 카드가 저장되지 않도록 그 노드가 들어오기
# 전까지 verified_cards가 비어 있고, 카드는 0개로 끝난다(경험 추출, 카드 후보, 청크 인덱싱까지만).
# embed_chunks/save_chunks(청크 검색 인덱싱)는 카드 저장 뒤에 붙고,
# 실패해도 error가 아니라 warnings만 남긴다.
#
# 에러 분기: 어느 노드든 state["error"]를 설정하면, 남은 노드를 건너뛰고 즉시 끝낸다.
# 노드 안에서 처리하지 못한 예외는 _safe가 error로 바꿔준다.
#
# 실패 정책(노드별):
#   fail-fast(error): parse(문서 하나라도 실패하면 중단 — 같은 job의 문서들을 함께 보고 경험을
#     묶으므로 문서가 빠지면 반쪽짜리 카드가 만들어질 수 있다), extract(호출 1번)
#   항목 단위 soft-fail(stage.finish_stage): draft_card, embed, save —
#     실패한 항목은 건너뛰고 warnings에 남기며, 처리하려던 항목이 전부 실패했을 때만 error
#   soft-fail(warnings만): embed_chunks, save_chunks —
#     청크 인덱싱은 보조 기능이라 job을 실패시키지 않는다
import functools
import inspect
import logging

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.logging import summarize_error
from app.domains.documents.pipeline.nodes.chunk import chunk
from app.domains.documents.pipeline.nodes.draft_card import draft_card
from app.domains.documents.pipeline.nodes.embed import embed
from app.domains.documents.pipeline.nodes.embed_chunks import embed_chunks
from app.domains.documents.pipeline.nodes.extract import extract
from app.domains.documents.pipeline.nodes.parse import parse
from app.domains.documents.pipeline.nodes.save import save
from app.domains.documents.pipeline.nodes.save_chunks import save_chunks
from app.domains.documents.pipeline.observe import NodeRun
from app.domains.documents.pipeline.state import IngestionState

logger = logging.getLogger(__name__)

NODE_ORDER = [
    "parse",
    "chunk",
    "extract",
    "draft_card",
    "embed",
    "save",
    "embed_chunks",
    "save_chunks",
]
_NODES = {
    "parse": parse,
    "chunk": chunk,
    "extract": extract,
    "draft_card": draft_card,
    "embed": embed,
    "save": save,
    "embed_chunks": embed_chunks,
    "save_chunks": save_chunks,
}


def _safe(name: str, node):
    # 노드가 try 밖에서 예기치 못한 예외를 내면 ainvoke()가 터져서
    # job이 failed로 바뀌지 않고 SSE가 영원히 "처리 중"으로 남는다.
    # 모든 노드를 감싸서 예외를 노드 이름이 담긴 error로 바꿔 한 곳에서 막는다.
    # 같은 자리에서 노드 실행 기록(NodeRun)도 남긴다.
    def failure(e: Exception) -> dict:
        logger.exception("%s 단계에서 예기치 못한 오류", name)
        return {"error": f"{name} 단계에서 예기치 못한 오류: {summarize_error(e)}"}

    if inspect.iscoroutinefunction(node):

        @functools.wraps(node)
        async def async_wrapper(state):
            run = NodeRun(name, state, is_last=name == NODE_ORDER[-1])
            try:
                try:
                    result = await node(state)
                    raised = False
                except Exception as e:
                    result, raised = failure(e), True
                run.log_done(result, raised)
                return result
            finally:
                run.close()

        return async_wrapper

    @functools.wraps(node)
    def wrapper(state):
        run = NodeRun(name, state, is_last=name == NODE_ORDER[-1])
        try:
            try:
                result = node(state)
                raised = False
            except Exception as e:
                result, raised = failure(e), True
            run.log_done(result, raised)
            return result
        finally:
            run.close()

    return wrapper


def _route_after(node: str):
    next_node = END if node == NODE_ORDER[-1] else NODE_ORDER[NODE_ORDER.index(node) + 1]

    def router(state: IngestionState) -> str:
        return END if state.get("error") else next_node

    return router


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(IngestionState)
    for name, node in _NODES.items():
        graph.add_node(name, _safe(name, node))

    graph.add_edge(START, NODE_ORDER[0])
    for node in NODE_ORDER:
        graph.add_conditional_edges(node, _route_after(node))

    return graph.compile()
