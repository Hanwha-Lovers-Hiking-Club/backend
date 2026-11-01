"""프롬프트를 고치며 반복 측정하는 dry-run: chunk -> extract -> draft_card -> verify 만 돌린다.

Storage/DB에 아무것도 쓰지 않고(임베딩, 저장 노드도 안 돈다) 로컬 파일을 바로 읽는다.
OpenAI만 호출한다. 프롬프트나 검증 로직을 바꾼 전후를 같은 문서로 비교할 때 쓴다.
프로젝트를 섞지 않도록 한 번에 프로젝트 하나.

사용법 (레포 루트에서):
    uv run python scripts/doc_test_dry.py --root "<데이터셋 폴더>" --project vigilantis \
        --docs "docs/adr/0004-rollback-runbook-whitelist-registration.md,docs/adr/0010-....md"
    (--docs를 생략하면 core 샘플 전부)
결과: --out 폴더(기본 runs/dry-<프로젝트>-<시각>)의 dry.md — 경험과 카드의 핵심 필드, 검증 판정.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doc_test_samples import SAMPLES  # noqa: E402
from doc_test_stage1 import resolve  # noqa: E402

from app.core.logging import job_log_context, setup_logging  # noqa: E402
from app.domains.documents.pipeline.nodes.chunk import chunk  # noqa: E402
from app.domains.documents.pipeline.nodes.draft_card import draft_card  # noqa: E402
from app.domains.documents.pipeline.nodes.extract import extract  # noqa: E402
from app.domains.documents.pipeline.nodes.parse import _extract_text  # noqa: E402
from app.domains.documents.pipeline.nodes.verify import verify  # noqa: E402
from app.domains.documents.pipeline.observe import (  # noqa: E402
    describe_total,
    finish_job_usage,
    start_job_usage,
)


def run(paths: list[Path]) -> tuple[dict, str]:
    job_id = uuid4()
    documents = [{"id": uuid4(), "path": p} for p in paths]
    state: dict = {
        "job_id": job_id,
        "parsed_documents": [
            {"document_id": d["id"], "text": _extract_text(d["path"].read_bytes(), d["path"].name)}
            for d in documents
        ],
    }
    with job_log_context(job_id):
        usage = start_job_usage(str(job_id))
        for node in (chunk, extract, draft_card, verify):
            update = node(state)
            if update.get("error"):
                raise SystemExit(f"{node.__name__} 실패: {update['error']}")
            state.update(update)
        summary = describe_total(usage)
        finish_job_usage(str(job_id))
    return state, summary


def render(state: dict, summary: str) -> str:
    chunks = state.get("chunks", [])
    elements = state.get("extracted_elements", [])
    verified = state.get("verified_cards", [])
    verdicts = [v.verification.verdict for v in verified]
    lines = ["# dry-run 결과", ""]
    lines += [
        f"- 청크 {len(chunks)} → 경험 {len(elements)} → 카드 {len(verified)}",
        f"- 검증: pass {verdicts.count('pass')} / rewrite {verdicts.count('rewrite')} / "
        f"discard {verdicts.count('discard')}",
        f"- {summary}",
        "",
        "## 경험 (extract 결과)",
        "",
    ]
    for i, e in enumerate(elements, 1):
        lines += [f"{i}. 문제: {e.problem}", f"   - outcome: {e.outcome}"]
    lines += ["", "## 카드", ""]
    for n, v in enumerate(verified, 1):
        d, r = v.draft, v.verification
        lines += [
            f"### {n}. [{r.verdict}] {d.title}",
            f"- context: {d.context}",
            f"- outcome: {d.outcome}",
            f"- project_date: {d.project_date}",
            f"- 검증 사유: {r.reason}",
        ]
        unsupported = getattr(r, "unsupported_claims", None)
        if unsupported:
            lines.append(f"- 근거 없는 주장: {unsupported}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--root", default=os.environ.get("DOC_TEST_ROOT"))
    parser.add_argument("--project", required=True, choices=sorted(SAMPLES))
    parser.add_argument("--docs", default="")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    if not args.root:
        raise SystemExit("--root 또는 환경변수 DOC_TEST_ROOT 가 필요하다")

    project_dir = next(iter(Path(args.root).glob(f"{args.project}-*-dataset")))
    specs = [s for s in args.docs.split(",") if s] or [s for s, _ in SAMPLES[args.project]["core"]]
    paths = [resolve(project_dir, s) for s in specs]
    out = Path(args.out or Path("runs") / f"dry-{args.project}-{datetime.now():%Y%m%d-%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()

    started = time.perf_counter()
    state, summary = run(paths)
    (out / "dry.md").write_text(render(state, summary), encoding="utf-8")
    (out / "verdicts.json").write_text(
        json.dumps(
            [
                {"title": v.draft.title, **v.verification.model_dump()}
                for v in state["verified_cards"]
            ],
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"완료 ({time.perf_counter() - started:.1f}초): {out / 'dry.md'}")


if __name__ == "__main__":
    main()
