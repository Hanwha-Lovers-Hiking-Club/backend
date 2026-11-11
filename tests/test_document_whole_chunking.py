"""소문서 통합 옵션의 경계, 원본 보존, 근거 허용 범위와 실행 예산을 검사한다."""

import asyncio
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.documents.pipeline.chunk_execution import prepare_extraction
from app.domains.documents.pipeline.chunking import chunk_document
from app.domains.documents.pipeline.document import (
    ChunkOptions,
    DocumentIR,
    ParsedBlock,
    ParsedDocument,
)
from app.domains.documents.pipeline.execution import initialize
from app.domains.documents.pipeline.execution_budget import estimate_execution_steps
from app.domains.documents.pipeline.llm import GenerationError
from app.domains.documents.pipeline.nodes.chunk import chunk
from app.domains.documents.pipeline.nodes.extract import make_extract_node
from app.domains.documents.pipeline.normalization import normalize_document

DOC_ID = UUID("4c10a229-9b3a-40bc-8611-e2f787540f03")


@pytest.fixture
def document():
    return normalize_document(
        ParsedDocument(
            document_id=DOC_ID,
            original_filename="decision.md",
            blocks=[
                ParsedBlock(
                    block_type="paragraph",
                    text="A service must retain two display rows.",
                    location="lines 1-2",
                    section_path=["Decision", "Context"],
                ),
                ParsedBlock(
                    block_type="table",
                    text="| option | result |\n| A | failed |\n| B | passed |\n",
                    location="lines 4-7",
                    section_path=["Decision", "Comparison"],
                ),
                ParsedBlock(
                    block_type="code",
                    text="  render()  \n",
                    location="lines 9-10",
                    section_path=["Decision", "Implementation"],
                ),
                ParsedBlock(
                    block_type="log",
                    text="[OK] two rows rendered.\n",
                    location="line 12",
                    section_path=["Decision", "Result"],
                ),
            ],
        )
    )


def joined(document):
    return "\n\n".join(b.text for b in document.blocks)


def test_zero_option_preserves_existing_chunks_exactly(document):
    assert chunk_document(document) == chunk_document(
        document, ChunkOptions(whole_document_chars=0)
    )
    assert len(chunk_document(document)) == 4


def test_whole_mode_keeps_every_original_block_and_uses_common_section_path(document):
    before = document.model_dump()
    result = chunk_document(document, ChunkOptions(whole_document_chars=12000))
    assert len(result) == 1
    assert result[0].blocks == document.blocks
    assert result[0].text == joined(document)
    assert result[0].section_path == ["Decision"]
    assert document.model_dump() == before
    assert chunk_document(document, ChunkOptions(whole_document_chars=12000)) == result
    assert [b.evidence_id for b in result[0].blocks] == list(document.evidence_index)


def test_threshold_is_inclusive_and_above_it_falls_back_to_existing_policy(document):
    size = len(joined(document))
    assert len(chunk_document(document, ChunkOptions(whole_document_chars=size))) == 1
    assert chunk_document(document, ChunkOptions(whole_document_chars=size - 1)) == chunk_document(
        document
    )


def test_whole_mode_can_exceed_soft_target_without_changing_oversized_meaning(document):
    result = chunk_document(document, ChunkOptions(target_chars=20, whole_document_chars=12000))
    assert len(result) == 1 and result[0].oversized


def test_whole_mode_with_no_common_heading_does_not_assign_the_first_section_to_everything(
    document,
):
    document.blocks[-1].section_path = ["Different root"]
    result = chunk_document(document, ChunkOptions(whole_document_chars=12000))
    assert result[0].section_path == []
    assert result[0].blocks[-1].section_path == ["Different root"]


def test_empty_document_still_has_no_chunks():
    document = DocumentIR(
        document_id=DOC_ID, original_filename="empty.md", blocks=[], evidence_index={}
    )
    assert chunk_document(document, ChunkOptions(whole_document_chars=12000)) == []


def test_threshold_counts_unicode_characters_not_file_bytes(document):
    document.blocks[0].text = "한글 문맥과 결과"
    size = len(joined(document))
    assert len(joined(document).encode("utf-8")) > size
    assert len(chunk_document(document, ChunkOptions(whole_document_chars=size))) == 1


@pytest.mark.parametrize("value", [-1, 1.5, True, "12000"])
def test_whole_document_limit_is_a_nonnegative_integer(value):
    with pytest.raises(ValidationError):
        ChunkOptions(whole_document_chars=value)


def test_node_switch_clears_old_chunk_progress_and_reduces_budget(document):
    state = initialize(
        {"sources": [{"document_id": DOC_ID, "original_filename": "decision.md", "path": "unused"}]}
    )
    state.update(documents=[document], gate_retry_budget=2)
    state.update(chunk(state))
    state.update(prepare_extraction(state))
    old_keys = set(state["chunk_progress"])
    old_budget = estimate_execution_steps(state)
    state["chunk_options"] = ChunkOptions(whole_document_chars=12000)
    state.update(chunk(state))
    assert state["chunk_progress"] == {}
    state.update(prepare_extraction(state))
    assert len(state["chunks"]) == len(state["chunk_progress"]) == 1
    assert not old_keys.intersection(state["chunk_progress"])
    assert estimate_execution_steps(state) < old_budget


def test_extract_can_cite_context_and_result_in_the_same_whole_document_chunk(document):
    class Extractor:
        async def generate(self, *, payload, output_type, **kwargs):
            assert len(payload["blocks"]) == 4
            refs = [payload["blocks"][i]["evidence_id"] for i in (0, 3)]
            return output_type.model_validate(
                {
                    "experiences": [
                        {
                            "context": {
                                "value": "Two display rows were required.",
                                "status": "confirmed",
                                "evidence_refs": [refs[0]],
                            },
                            "outcome": {
                                "value": "Two rows rendered.",
                                "status": "confirmed",
                                "evidence_refs": [refs[1]],
                            },
                        }
                    ]
                }
            )

    result = asyncio.run(
        make_extract_node(Extractor())(
            {
                "documents": [document],
                "chunks": chunk_document(document, ChunkOptions(whole_document_chars=12000)),
            }
        )
    )
    experience = result["extraction_result"].experiences[0]
    assert set(experience.evidence_index) == {
        document.blocks[0].evidence_id,
        document.blocks[3].evidence_id,
    }


def test_whole_mode_does_not_bypass_serialized_llm_request_limit(document):
    class Forbidden:
        async def generate(self, **kwargs):
            pytest.fail("An oversized request must fail before calling the model")

    with pytest.raises(GenerationError) as caught:
        asyncio.run(
            make_extract_node(Forbidden(), max_input_chars=100)(
                {
                    "documents": [document],
                    "chunks": chunk_document(document, ChunkOptions(whole_document_chars=12000)),
                }
            )
        )
    assert caught.value.code == "INPUT_TOO_LARGE"
