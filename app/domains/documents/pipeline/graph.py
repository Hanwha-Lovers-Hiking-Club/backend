# 파이프라인 전체 연결:
#   parse -> chunk -> extract -> draft_card -> verify -> embed -> save
#   -> embed_chunks -> save_chunks
#
# 카드 경로(extract ~ save)와 원문 검색 인덱싱(embed_chunks, save_chunks)은 독립이다.
# 카드가 실패해도 읽은 문서는 챗봇이 원문으로 쓸 수 있어야 해서, 라우터가 두 군데에서
# 인덱싱으로 바로 건너뛴다.
#   - 카드 노드의 오류는 _card_step이 state["card_error"]로 바꿔 담고, 남은 카드 노드를 건너뛴다
#   - parse가 일부 문서를 못 읽었으면(parse_failures) 카드 경로를 통째로 건너뛴다.
#     빠진 문서가 핵심 근거였을 수 있어서 반쪽 근거로 카드를 만들지 않는다
#   - 그 밖의 노드가 state["error"]를 설정하면 즉시 끝낸다(parse는 모든 문서를 못 읽었을 때만 error)
# 노드 안에서 처리하지 못한 예외는 _safe가 error로 바꾼다(카드 노드면 다시 card_error가 된다).
# 저장할 카드는 verify가 정한 공개 상태(active/draft)가 있는 것뿐이다
# (excluded/incomplete는 저장 안 함).
# job을 실패로 볼지(쓸 수 있는 결과가 하나도 없는지)는 documents/service.py가 최종 state로 정한다.
#
# 실패 정책(노드별):
#   문서 단위 soft-fail: parse — 읽지 못한 문서는 건너뛰고 warnings에 남긴다
#   항목 단위 soft-fail(stage.finish_stage): draft_card, verify, embed, save —
#     실패한 항목은 건너뛰고 warnings에 남기며, 처리하려던 항목이 전부 실패했을 때만 카드 경로 오류
#   soft-fail(warnings만): embed_chunks, save_chunks —
#     인덱싱이 실패하면 warnings에 남는다(카드도 0개면 service가 job을 실패시킨다)
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
from app.domains.documents.pipeline.nodes.verify import verify
from app.domains.documents.pipeline.observe import NodeRun
from app.domains.documents.pipeline.state import IngestionState

logger = logging.getLogger(__name__)

# 오류가 나도 job을 끝내지 않고 청크 인덱싱으로 넘어가는 카드 경로 노드와, 그때 갈 노드
CARD_NODES = ("extract", "draft_card", "verify", "embed", "save")
_CARD_EXIT = "embed_chunks"

NODE_ORDER = [
    "parse",
    "chunk",
    "extract",
    "draft_card",
    "verify",
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
    "verify": verify,
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


def _card_step(node):
    # 카드 노드의 error를 card_error로 바꿔서, job을 끝내지 않고 청크 인덱싱으로 넘어가게 한다
    def to_card_error(result: dict) -> dict:
        if not result.get("error"):
            return result
        logger.warning("카드 경로 오류, 청크 인덱싱으로 넘어감: %s", result["error"])
        return {**{k: v for k, v in result.items() if k != "error"}, "card_error": result["error"]}

    if inspect.iscoroutinefunction(node):

        @functools.wraps(node)
        async def async_wrapper(state):
            return to_card_error(await node(state))

        return async_wrapper

    @functools.wraps(node)
    def wrapper(state):
        return to_card_error(node(state))

    return wrapper


def _route_after(node: str):
    next_node = END if node == NODE_ORDER[-1] else NODE_ORDER[NODE_ORDER.index(node) + 1]

    def router(state: IngestionState) -> str:
        if state.get("error"):
            return END
        if node == "chunk" and state.get("parse_failures"):
            return _CARD_EXIT
        if node in CARD_NODES and state.get("card_error"):
            return _CARD_EXIT
        return next_node

    return router


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(IngestionState)
    for name, node in _NODES.items():
        step = _safe(name, node)
        graph.add_node(name, _card_step(step) if name in CARD_NODES else step)

    graph.add_edge(START, NODE_ORDER[0])
    for node in NODE_ORDER:
        graph.add_conditional_edges(node, _route_after(node))

    return graph.compile()
