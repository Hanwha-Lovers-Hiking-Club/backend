# LangGraph StateGraph 조립만 한다 (노드 연결, 조건부 엣지, 검증 실패 시 재작성 루프).
# 노드 로직은 쓰지 않고 nodes/ 의 함수를 가져다 연결만 한다.

from langgraph.graph import END, START, StateGraph

from app.domains.documents.pipeline.chunk_execution import make_chunk_worker, next_chunk
from app.domains.documents.pipeline.document_lifecycle import make_document_embedder, next_document
from app.domains.documents.pipeline.draft_execution import make_draft_worker
from app.domains.documents.pipeline.execution import next_step, retry_target
from app.domains.documents.pipeline.nodes.build_embedding_texts import build_embedding_texts
from app.domains.documents.pipeline.nodes.chunk import chunk
from app.domains.documents.pipeline.nodes.complete_document import complete_document
from app.domains.documents.pipeline.nodes.draft_card import draft_card
from app.domains.documents.pipeline.nodes.embed import embed
from app.domains.documents.pipeline.nodes.extract import extract
from app.domains.documents.pipeline.nodes.finish import finish
from app.domains.documents.pipeline.nodes.initialize import initialize
from app.domains.documents.pipeline.nodes.merge_extraction import merge_extraction
from app.domains.documents.pipeline.nodes.normalize import normalize
from app.domains.documents.pipeline.nodes.parse import parse
from app.domains.documents.pipeline.nodes.prepare_extraction import prepare_extraction
from app.domains.documents.pipeline.nodes.prepare_retry import prepare_retry
from app.domains.documents.pipeline.nodes.save import make_save_node
from app.domains.documents.pipeline.nodes.select_candidates import select_candidates
from app.domains.documents.pipeline.nodes.settle import settle
from app.domains.documents.pipeline.nodes.verify import verify
from app.domains.documents.pipeline.state import IngestionInput, IngestionState


def build_graph(
    *,
    extract_node=extract,
    draft_node=draft_card,
    verify_node=verify,
    initialize_node=initialize,
    embed_node=embed,
    document_saver=None,
    checkpointer=None,
    interrupt_before=None,
):
    builder = StateGraph(IngestionState, input_schema=IngestionInput)
    for name, node in {
        "initialize": initialize_node,
        "parse": parse,
        "normalize": normalize,
        "chunk": chunk,
        "prepare_extraction": prepare_extraction,
        "extract": make_chunk_worker(extract_node),
        "merge_extraction": merge_extraction,
        "select_candidates": select_candidates,
        "draft_card": make_draft_worker(draft_node),
        "verify": verify_node,
        "settle": settle,
        "prepare_retry": prepare_retry,
        "finish": finish,
        "build_embedding_texts": build_embedding_texts,
        "embed": make_document_embedder(embed_node),
        "save": make_save_node(document_saver),
        "complete_document": complete_document,
    }.items():
        builder.add_node(name, node)
    for source, target in [
        (START, "initialize"),
        ("initialize", "parse"),
        ("parse", "normalize"),
        ("normalize", "chunk"),
        ("chunk", "prepare_extraction"),
        ("prepare_extraction", "extract"),
        ("merge_extraction", "select_candidates"),
        ("select_candidates", "draft_card"),
        ("draft_card", "verify"),
        ("verify", "settle"),
        ("finish", "build_embedding_texts"),
        ("build_embedding_texts", "embed"),
        ("embed", "save"),
        ("save", "complete_document"),
    ]:
        builder.add_edge(source, target)
    builder.add_conditional_edges(
        "extract", next_chunk, {"extract": "extract", "merge_extraction": "merge_extraction"}
    )
    builder.add_conditional_edges(
        "complete_document", next_document, {"embed": "embed", "end": END}
    )
    builder.add_conditional_edges(
        "settle", next_step, {"prepare_retry": "prepare_retry", "finish": "finish"}
    )
    builder.add_conditional_edges(
        "prepare_retry",
        retry_target,
        {
            name: ("prepare_extraction" if name == "extract" else name)
            for name in ("parse", "normalize", "chunk", "extract", "draft_card", "settle")
        },
    )
    return builder.compile(
        checkpointer=checkpointer, interrupt_before=interrupt_before, name="document_ingestion"
    ).with_config(recursion_limit=1000)


def get_graph():
    """Agent Server owns checkpointing. Normal Python callers can inject their own saver."""
    from app.domains.knowledge_cards.service import save_document_cards

    return build_graph(document_saver=save_document_cards)
