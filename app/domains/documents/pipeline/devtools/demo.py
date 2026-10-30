# 실제 LangGraph에 명시적인 예제 모델 응답을 연결한 데모다. 외부 API나 DB는 호출하지 않는다.

from pathlib import Path
from uuid import UUID

from app.domains.documents.pipeline.execution import initialize
from app.domains.documents.pipeline.graph import build_graph
from app.domains.documents.pipeline.nodes.draft_card import make_draft_node
from app.domains.documents.pipeline.nodes.extract import make_extract_node
from app.domains.documents.pipeline.nodes.verify import make_verify_node
from app.domains.documents.pipeline.verification.models import VerificationOptions


class DemoGenerator:
    async def generate(self, *, instructions, payload, output_type):
        if "blocks" in payload:
            blocks = payload["blocks"]
            values = {
                name: {
                    "value": block["text"].strip(),
                    "status": "confirmed",
                    "evidence_refs": [block["evidence_id"]],
                }
                for name, block in zip(
                    ("context", "problem", "cause", "approach", "outcome", "lesson"),
                    blocks,
                    strict=True,
                )
            }
            return output_type.model_validate(
                {
                    "experiences": [
                        {**values, "technologies": ["FastAPI"], "keywords": ["dependency"]}
                    ]
                }
            )
        if "extraction" in payload:
            source = payload["extraction"]
            title = (
                source["problem"]["value"]
                if payload.get("retry_feedback")
                else "Redis fixed every failure."
            )
            return output_type.model_validate(
                {
                    "draft": {
                        "title": title,
                        "summary": source["outcome"]["value"],
                        "lesson": source["lesson"]["value"],
                        "keywords": ["dependency"],
                    },
                    "skip_reason": None,
                }
            )
        return output_type.model_validate(
            {
                "assessments": [
                    {
                        "key": claim["key"],
                        "verdict": "unsupported" if "Redis" in claim["value"] else "supported",
                        "evidence_refs": claim["evidence_refs"],
                        "reason": "Synthetic demo check against the supplied example",
                        "failure_kind": "claim" if "Redis" in claim["value"] else None,
                    }
                    for claim in payload["claims"]
                ]
            }
        )


def demo_initialize(state):
    return initialize(
        {
            **state,
            "sources": [
                {
                    "document_id": UUID("4c10a229-9b3a-40bc-8611-e2f787540f03"),
                    "original_filename": "demo.md",
                    "path": Path(__file__).with_name("demo.md"),
                }
            ],
        }
    )


def get_demo_graph():
    generator = DemoGenerator()
    return build_graph(
        initialize_node=demo_initialize,
        extract_node=make_extract_node(generator),
        draft_node=make_draft_node(generator),
        verify_node=make_verify_node(
            options=VerificationOptions(llm_enabled=True),
            generator=generator,
        ),
    )
