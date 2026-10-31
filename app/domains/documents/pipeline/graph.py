# 파이프라인 전체 연결:
# parse -> chunk -> extract -> draft_card -> verify -> embed -> save
# -> embed_chunks -> save_chunks.
# embed_chunks/save_chunks(청크 검색 인덱싱)는 카드 저장 뒤에 붙고,
# 실패해도 error가 아니라 warnings만 남긴다.
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
# 노드 안에서 처리하지 못한 예외는 _safe가 error로 바꿔준다.
#
# 실패 정책(노드별):
#   fail-fast(error): parse(문서 하나라도 실패하면 중단 — extract가 모든 문서를 한꺼번에 보고 경험을
#     묶으므로 문서가 빠지면 반쪽짜리 카드가 만들어질 수 있다), extract(호출 1번)
#   항목 단위 soft-fail(stage.finish_stage): draft_card, verify, embed, save —
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
from app.domains.documents.pipeline.nodes.verify import verify
from app.domains.documents.pipeline.observe import NodeRun
from app.domains.documents.pipeline.state import IngestionState

logger = logging.getLogger(__name__)

_NODE_ORDER = [
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
            run = NodeRun(name, state, is_last=name == _NODE_ORDER[-1])
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
        run = NodeRun(name, state, is_last=name == _NODE_ORDER[-1])
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
    next_node = END if node == _NODE_ORDER[-1] else _NODE_ORDER[_NODE_ORDER.index(node) + 1]

    def router(state: IngestionState) -> str:
        return END if state.get("error") else next_node

    return router


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(IngestionState)
    for name, node in _NODES.items():
        graph.add_node(name, _safe(name, node))

    graph.add_edge(START, _NODE_ORDER[0])
    for node in _NODE_ORDER:
        graph.add_conditional_edges(node, _route_after(node))

    return graph.compile()
