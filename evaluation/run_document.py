# 문서를 실제 LangGraph로 처리하되 임베딩과 DB 저장은 하지 않고 평가 입력을 파일로 남긴다.
# 재시도가 첫 초안을 덮어쓸 수 있으므로 최종 State뿐 아니라 노드별 update를 모두 기록한다.
# 사용법은 evaluation/README.md를 따른다.

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from pydantic_core import to_jsonable_python

from app.config import settings
from app.domains.documents.pipeline.graph import build_graph
from app.domains.documents.pipeline.llm import GenerationError, configured_generator
from app.domains.documents.pipeline.nodes.draft_card import make_draft_node
from app.domains.documents.pipeline.nodes.extract import make_extract_node
from app.domains.documents.pipeline.nodes.verify import make_verify_node
from app.domains.documents.pipeline.verification.models import VerificationOptions

PROMPT_DIR = Path("app/domains/documents/pipeline/prompts")
DB_MODULES = ("supabase", "app.domains.knowledge_cards.service")


class RecordingGenerator:
    """Records model, latency and token usage. Payloads stay out of this log."""

    def __init__(self, stage, calls):
        self.stage = stage
        self.calls = calls

    async def generate(self, *, instructions, payload, output_type):
        call = {"stage": self.stage, "output_type": output_type.__name__, "usage": None}
        started = time.monotonic()
        try:
            async with configured_generator(self.stage) as generator:
                call["model"] = generator.model
                client = getattr(generator, "client", None)
                if hasattr(client, "responses"):
                    original = client.responses.create

                    async def create(**kwargs):
                        response = await original(**kwargs)
                        call["usage"] = to_jsonable_python(response.usage, fallback=str)
                        return response

                    generator.client = SimpleNamespace(responses=SimpleNamespace(create=create))
                return await generator.generate(
                    instructions=instructions, payload=payload, output_type=output_type
                )
        except GenerationError as exc:
            call["error"] = exc.code
            raise
        finally:
            call["seconds"] = round(time.monotonic() - started, 2)
            self.calls.append(call)


async def skip_embedding(state):
    # 카드 품질 평가에는 벡터가 필요 없다. 가짜 벡터도 만들지 않는다.
    return {"embeddings": [], "embedding_usage": []}


def build_evaluation_graph(calls):
    max_chars = settings.DOCUMENT_LLM_MAX_INPUT_CHARS
    return build_graph(
        extract_node=make_extract_node(
            RecordingGenerator("extract", calls), max_input_chars=max_chars
        ),
        draft_node=make_draft_node(RecordingGenerator("draft", calls), max_input_chars=max_chars),
        verify_node=make_verify_node(
            options=VerificationOptions(
                rules_enabled=settings.VERIFY_RULES_ENABLED,
                llm_enabled=settings.VERIFY_LLM_ENABLED,
                max_retries=settings.VERIFY_MAX_RETRIES,
                max_input_chars=max_chars,
            ),
            generator=RecordingGenerator("verify", calls),
        ),
        embed_node=skip_embedding,
    )


def first_drafts(events):
    found = {}
    for event in events:
        for draft in (event["update"] or {}).get("drafts", []):
            found.setdefault(draft["experience_id"], draft)
    return list(found.values())


def usage_totals(calls):
    totals = {}
    for call in calls:
        item = totals.setdefault(
            call["stage"], {"calls": 0, "errors": 0, "seconds": 0.0, "input": 0, "output": 0}
        )
        item["calls"] += 1
        item["errors"] += "error" in call
        item["seconds"] = round(item["seconds"] + call["seconds"], 2)
        usage = call["usage"] or {}
        item["input"] += usage.get("input_tokens", 0)
        item["output"] += usage.get("output_tokens", 0)
    return totals


def run_metadata(path):
    prompts = {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in PROMPT_DIR.glob("*.md")
    }
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        "started_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "prompt_sha256": prompts,
        "provider": settings.LLM_PROVIDER,
        "models": {
            "extract": settings.EXTRACT_MODEL,
            "draft": settings.DRAFT_MODEL,
            "verify": settings.VERIFY_MODEL,
        },
        "verify_llm_enabled": settings.VERIFY_LLM_ENABLED,
        "verify_max_retries": settings.VERIFY_MAX_RETRIES,
    }


async def run(args):
    path = args.document.resolve()
    if not path.is_file():
        raise SystemExit(f"문서를 찾을 수 없습니다: {path}")
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    # 같은 파일은 어느 회차에서든 같은 ID를 받아 결과를 문서 단위로 비교할 수 있다.
    document_id = uuid5(NAMESPACE_URL, f"evaluation:{args.document_key}")
    source = {
        "document_id": document_id,
        "original_filename": path.name,
        "path": path,
    }
    summary = {
        "document_key": args.document_key,
        "document_id": str(document_id),
        **run_metadata(path),
    }
    calls, events, state = [], [], {}
    started = time.monotonic()
    error = None
    try:
        async for chunk in build_evaluation_graph(calls).astream(
            {"sources": [source]}, stream_mode="updates"
        ):
            for node, update in chunk.items():
                record = to_jsonable_python(update, fallback=str)
                events.append(
                    {
                        "seq": len(events),
                        "node": node,
                        "seconds": round(time.monotonic() - started, 2),
                        "update": record,
                    }
                )
                state.update(record or {})
                print(f"[{events[-1]['seconds']:>7}s] {node}", flush=True)
    except Exception as exc:
        # 실패 전까지의 기록은 원인 분석 자료이므로 버리지 않는다.
        error = f"{type(exc).__name__}: {exc}"
    drafts = first_drafts(events)
    summary.update(
        {
            "error": error,
            "seconds": round(time.monotonic() - started, 2),
            "usage": usage_totals(calls),
            "node_counts": {
                node: sum(event["node"] == node for event in events)
                for node in dict.fromkeys(event["node"] for event in events)
            },
            "chunks": len(state.get("all_chunks", [])),
            "first_drafts": len(drafts),
            "verified_drafts": len(state.get("verified_drafts", [])),
            "review_drafts": len(state.get("review_drafts", [])),
            "skips": len(state.get("draft_skips", [])),
            "decisions": [
                {"experience_id": item["experience_id"], "decision": item["decision"]}
                for item in state.get("verification_results", [])
            ],
            "document_progress": state.get("document_progress", {}),
            "processing_failures": state.get("processing_failures", []),
            "embedding_calls": len(state.get("embedding_usage", [])),
            "db_modules_loaded": [name for name in DB_MODULES if name in sys.modules],
        }
    )
    with (output / "events.jsonl").open("w", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
    for name, value in {
        "calls.json": calls,
        "first_drafts.json": drafts,
        "final_state.json": state,
        "summary.json": summary,
    }.items():
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if error else 0


def parse_args():
    parser = argparse.ArgumentParser(description="저장 없이 문서 1개를 평가용으로 처리합니다.")
    parser.add_argument("--document", type=Path, required=True, help="입력 문서 경로")
    parser.add_argument(
        "--document-key",
        required=True,
        help="평가 전체에서 유일한 문서 키. 예: ssafesta/webgl-stutter-diagnosis",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="결과 폴더. evaluation/runs 아래를 사용"
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
