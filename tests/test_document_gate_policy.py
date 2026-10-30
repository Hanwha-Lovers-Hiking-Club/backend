# 합성 문장과 최소 PDF로 의미 검증 기본 정책 및 미해결 보류·실패 범위를 확인한다.
# 모의 모델의 판정은 테스트용 정답이며 실제 LLM 정확도나 대표 문서 보류율을 측정하지 않는다.

import asyncio
import copy
from io import BytesIO

import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.domains.documents.pipeline.document import ParsedBlock, ParsedDocument
from app.domains.documents.pipeline.drafting import draft_state
from app.domains.documents.pipeline.embeddings.models import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingSpec,
    EmbeddingUsage,
)
from app.domains.documents.pipeline.extraction import experience_id
from app.domains.documents.pipeline.graph import build_graph
from app.domains.documents.pipeline.llm import GenerationError
from app.domains.documents.pipeline.models import ExtractedElements, ExtractionResult
from app.domains.documents.pipeline.nodes.build_embedding_texts import build_embedding_texts
from app.domains.documents.pipeline.nodes.draft_card import make_draft_node
from app.domains.documents.pipeline.nodes.embed import make_embed_node
from app.domains.documents.pipeline.nodes.extract import make_extract_node
from app.domains.documents.pipeline.nodes.verify import make_verify_node, verify
from app.domains.documents.pipeline.normalization import normalize_document
from app.domains.documents.pipeline.verification.consistency_checker import literal_match
from app.domains.documents.pipeline.verification.gate import verify_state
from app.domains.documents.pipeline.verification.models import Claim, VerificationOptions

DOC_ID = "4c10a229-9b3a-40bc-8611-e2f787540f03"
KOREAN_FACTS = {
    "context": "FastAPI 서비스에서 결제 요청을 처리했다.",
    "problem": "데이터베이스 장애로 요청이 지연됐다.",
    "cause": "데이터베이스 연결이 끊겼다.",
    "approach": "상태 점검을 추가해 장애를 감지했다.",
    "rationale": "의존 서비스의 장애를 빠르게 확인하려고 했다.",
    "outcome": "장애 감지 후 재연결해 요청 처리가 정상화됐다.",
    "lesson": "의존 서비스 상태를 점검하면 장애 원인을 빠르게 확인할 수 있다.",
}


class ParaphrasingWriter:
    async def generate(self, *, instructions, payload, output_type):
        return output_type.model_validate(
            {
                "draft": {
                    "title": "DB 상태 점검으로 결제 요청 지연 해소",
                    "summary": "연결 단절을 감지하고 복구해 API 요청 처리를 정상화한 경험이다.",
                    "lesson": "외부 의존성의 상태 확인을 장애 대응 절차에 포함하자.",
                    "keywords": ["장애 대응"],
                },
                "skip_reason": None,
            }
        )


class LabelledVerifier:
    def __init__(self, verdict="supported", key="draft.summary", failure=False):
        self.verdict, self.key, self.failure = verdict, key, failure
        self.calls = []

    async def generate(self, *, instructions, payload, output_type):
        self.calls.append(copy.deepcopy(payload))
        if self.failure:
            raise GenerationError("LLM_API_ERROR", "Synthetic verifier failure")
        return output_type.model_validate(
            {
                "assessments": [
                    {
                        "key": claim["key"],
                        "verdict": self.verdict if claim["key"] == self.key else "supported",
                        "evidence_refs": claim["evidence_refs"],
                        "reason": "Explicitly labelled synthetic test case",
                        "failure_kind": None,
                    }
                    for claim in payload["claims"]
                ],
            }
        )


@pytest.fixture
def paraphrased_state():
    document = normalize_document(
        ParsedDocument(
            document_id=DOC_ID,
            original_filename="synthetic-incident.txt",
            blocks=[
                ParsedBlock(block_type="paragraph", text=text, location=f"line {index + 1}")
                for index, text in enumerate(KOREAN_FACTS.values())
            ],
        )
    )
    fields = {
        name: {"value": text, "status": "confirmed", "evidence_refs": [block.evidence_id]}
        for (name, text), block in zip(KOREAN_FACTS.items(), document.blocks, strict=True)
    }
    # 근거와 의미가 같지만 문장 표현은 다른 Extract 출력도 포함한다.
    fields["cause"]["value"] = "DB 연결 단절이 지연의 원인이었다."
    source = ExtractedElements(
        **fields, evidence_index=document.evidence_index, technologies=["FastAPI"]
    )
    state = {"documents": [document], "extraction_result": ExtractionResult(experiences=[source])}
    state.update(asyncio.run(draft_state(state, ParaphrasingWriter())))
    return state


def test_semantic_verification_is_default_but_rules_remain_mandatory(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("VERIFY_LLM_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.VERIFY_LLM_ENABLED is True and settings.VERIFY_RULES_ENABLED is True
    assert VerificationOptions().llm_enabled is True


@pytest.mark.parametrize(
    "verdict,key,expected,target",
    [
        ("supported", "draft.summary", "pass", None),
        ("unsupported", "draft.summary", "retry", "draft_card"),
        ("unsupported", "extract.cause", "retry", "extract"),
        ("uncertain", "draft.summary", "needs_review", None),
        ("conflicted", "draft.summary", "needs_review", None),
    ],
)
def test_paraphrases_follow_semantic_verdict_without_relaxing_rules(
    paraphrased_state, verdict, key, expected, target
):
    before = copy.deepcopy(paraphrased_state)
    model = LabelledVerifier(verdict, key)
    result = asyncio.run(verify_state(paraphrased_state, generator=model))
    assert len(model.calls) == 1
    assert result["verification_results"][0].decision == expected
    assert result["verification_results"][0].retry_node == target
    assert {item["key"] for item in model.calls[0]["claims"]} >= {
        "draft.title",
        "draft.summary",
        "draft.lesson",
        "extract.cause",
    }
    assert paraphrased_state == before
    assert bool(result["verified_drafts"]) == (expected == "pass")


def test_explicit_rules_only_mode_still_holds_paraphrases_without_model_call(paraphrased_state):
    model = LabelledVerifier()
    result = asyncio.run(
        verify_state(
            paraphrased_state, options=VerificationOptions(llm_enabled=False), generator=model
        )
    )
    assert not model.calls
    assert result["verification_results"][0].decision == "needs_review"
    fields = {
        issue.field
        for issue in result["verification_results"][0].issues
        if issue.code == "SEMANTIC_CHECK_REQUIRED"
    }
    assert fields >= {"title", "summary", "cause", "lesson"}


def test_default_node_routes_paraphrases_to_configured_verifier(monkeypatch, paraphrased_state):
    import importlib

    from app.config import settings

    monkeypatch.setattr(settings, "VERIFY_LLM_ENABLED", True)
    module = importlib.import_module("app.domains.documents.pipeline.nodes.verify")
    model, stages = LabelledVerifier(), []

    def configured(stage):
        stages.append(stage)
        return model

    monkeypatch.setattr(module, "ConfiguredGenerator", configured)
    result = asyncio.run(verify(paraphrased_state))
    assert stages == ["verify"] and len(model.calls) == 1
    assert result["verification_results"][0].decision == "pass"


@pytest.mark.parametrize("with_generator", [True, False])
def test_semantic_failure_or_missing_verifier_never_becomes_pass(paraphrased_state, with_generator):
    model = LabelledVerifier(failure=True) if with_generator else None
    result = asyncio.run(verify_state(paraphrased_state, generator=model))
    assert result["verification_results"][0].decision == "needs_review"
    assert not result["verified_drafts"]


@pytest.mark.parametrize(
    "excerpt,claim_value,expected",
    [
        (
            "데이터베이스 연결이 끊겼다.\n캐시 연결은 정상이었다.",
            "데이터베이스 연결이 끊겼다.",
            True,
        ),
        (
            "데이터베이스 연결이 끊겼다.\n캐시에는 문제가 발생하지 않았다.",
            "데이터베이스 연결이 끊겼다.",
            False,
        ),
        (
            "데이터베이스에는 문제가 발생하지 않았다.",
            "데이터베이스에는 문제가 발생하지 않았다.",
            True,
        ),
        ("가설:\n데이터베이스 연결이 끊겼다.", "데이터베이스 연결이 끊겼다.", False),
        (
            "데이터베이스 연결이 끊겼다.\n아니다. 네트워크가 원인이었다.",
            "데이터베이스 연결이 끊겼다.",
            False,
        ),
    ],
)
def test_characterizes_korean_negation_scope_without_changing_policy(
    excerpt, claim_value, expected
):
    # 관련 없는 부정 문장도 차단하는 현 동작을 기록한다. 개선 정책의 정답으로 고정한 것은 아니다.
    claim = Claim(
        key="extract.cause",
        field="cause",
        value=claim_value,
        status="confirmed",
        evidence_refs=["e1"],
    )
    assert literal_match(claim, {"e1": {"evidence": {"excerpt": excerpt}}}) is expected


PDF_FACTS = {
    "context": "FastAPI service handles requests.",
    "problem": "Requests stalled.",
    "cause": "Database connection failed.",
    "approach": "Added a health check.",
    "rationale": "Detect dependency failures.",
    "outcome": "Requests recovered.",
    "lesson": "Check dependency health.",
}


def incident_pdf(image_page):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=(600, 800))
    for page in (1, 2):
        pdf.setFont("Helvetica", 11)
        lines = PDF_FACTS.values() if page == 1 else ["Appendix contains deployment notes."]
        for index, line in enumerate(lines):
            pdf.drawString(40, 740 - index * 35, line)
        if page == image_page:
            pdf.drawImage(ImageReader(Image.new("RGB", (8, 8), "black")), 550, 760, 16, 16)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


class PdfExperienceModel(LabelledVerifier):
    async def generate(self, *, instructions, payload, output_type):
        if "blocks" in payload:
            if not all(
                any(text in block["text"] for block in payload["blocks"])
                for text in PDF_FACTS.values()
            ):
                return output_type.model_validate({"experiences": []})
            return output_type.model_validate(
                {
                    "experiences": [
                        {
                            **{
                                name: {
                                    "value": text,
                                    "status": "confirmed",
                                    "evidence_refs": [
                                        next(
                                            block["evidence_id"]
                                            for block in payload["blocks"]
                                            if text in block["text"]
                                        )
                                    ],
                                }
                                for name, text in PDF_FACTS.items()
                            },
                            "technologies": ["FastAPI"],
                            "keywords": [],
                        }
                    ]
                }
            )
        if "extraction" in payload:
            return output_type.model_validate(
                {
                    "draft": {
                        "title": "Recovery using a dependency health check",
                        "summary": "Detected database failures and restored requests.",
                        "lesson": PDF_FACTS["lesson"],
                        "keywords": [],
                    },
                    "skip_reason": None,
                }
            )
        return await super().generate(
            instructions=instructions, payload=payload, output_type=output_type
        )


@pytest.mark.parametrize("image_page", [None, 1, 2])
def test_pdf_image_warning_full_graph_scope_with_image_free_control(tmp_path, image_page):
    path = tmp_path / "synthetic-incident.pdf"
    path.write_bytes(incident_pdf(image_page))
    model = PdfExperienceModel()

    async def no_external_embedding(state):
        assert bool(state["verified_drafts"]) == (image_page is None)
        return {"embeddings": [], "embedding_usage": []}

    graph = build_graph(
        extract_node=make_extract_node(model),
        draft_node=make_draft_node(model),
        verify_node=make_verify_node(generator=model),
        embed_node=no_external_embedding,
    )
    result = asyncio.run(
        graph.ainvoke(
            {
                "sources": [
                    {
                        "document_id": DOC_ID,
                        "original_filename": path.name,
                        "path": str(path),
                    }
                ]
            }
        )
    )
    assert len(result["drafts"]) == 1
    assert result["drafts"][0].experience_id == experience_id(result["drafts"][0].source_extraction)
    assert all(
        ref.location.startswith("p.1")
        for ref in result["drafts"][0].source_extraction.evidence_index.values()
    )
    if image_page is None:
        assert not result["documents"][0].warnings and len(model.calls) == 1
        assert result["verification_results"][0].decision == "pass"
    else:
        assert f"IMAGES_NOT_TRANSCRIBED:p.{image_page}" in result["documents"][0].warnings
        assert not model.calls and not result["verified_drafts"]
        assert result["verification_results"][0].decision == "needs_review"
        assert any(
            issue.code == "INCOMPLETE_SOURCE" for issue in result["verification_results"][0].issues
        )


def test_one_long_embedding_view_currently_fails_batch_and_char_guard_can_hold_it(
    paraphrased_state,
):
    state = copy.deepcopy(paraphrased_state)
    state.update(asyncio.run(verify_state(state, generator=LabelledVerifier())))
    state.update(build_embedding_texts(state))
    lengths = sorted(len(text.text) for text in state["embedding_texts"][0].texts.values())
    limit = lengths[-2]
    assert sum(len(text.text) > limit for text in state["embedding_texts"][0].texts.values()) == 1
    spec = EmbeddingSpec(provider="synthetic", model="character-limit-test", dimensions=3)

    class LengthLimitedClient:
        def __init__(self):
            self.calls = []

        async def embed(self, texts):
            self.calls.append(list(texts))
            if any(len(text) > limit for text in texts):
                raise EmbeddingError("EMBEDDING_INPUT_TOO_LARGE", status_code=400)
            return EmbeddingBatch(
                model=spec.model,
                dimensions=3,
                vectors=[[1.0, 0.0, 0.0] for _ in texts],
                usage=EmbeddingUsage(prompt_tokens=1, total_tokens=1),
            )

    client = LengthLimitedClient()
    node = make_embed_node(client, spec=spec)
    with pytest.raises(EmbeddingError, match="EMBEDDING_INPUT_TOO_LARGE"):
        asyncio.run(node(state))
    assert len(client.calls) == 1 and len(client.calls[0]) == 4 and state["embeddings"] == []
    # 테스트의 문자 수 제한이다. 실제 모델 토큰 수를 계산하거나 실패 입력을 자동 식별한 것은 아니다.
    state["embedding_text_options"] = {"max_chars": limit}
    state.update(build_embedding_texts(state))
    result = asyncio.run(node(state))
    assert len(client.calls[-1]) == 3
    assert state["embedding_texts"][0].skipped["content"].code == "INPUT_TOO_LARGE"
    assert "content" not in result["embeddings"][0].vectors
