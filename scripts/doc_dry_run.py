# ruff: noqa: E501 — 리포트 줄을 만드는 f-string이 길다(개발용 스크립트)
"""파이프라인 dry-run: 로컬 프로젝트 폴더마다 parse -> chunk -> extract -> draft_card -> verify를 실제로 돌린다.

DB/Storage에 쓰지 않고(임베딩, 저장 노드도 안 돈다) OpenAI만 호출한다. 폴더 하나 = 업로드 묶음 하나다.
evaluation/ 폴더는 읽지 않는다. 결과를 정답과 비교하는 건 사람이 report.md를 보고 한다.

사용법 (레포 루트에서):
    uv run python scripts/doc_dry_run.py "<프로젝트 폴더>" ["<프로젝트 폴더>" ...]
결과: --out 폴더(기본 runs/dry/<시각>)의 <폴더명>.md, <폴더명>.pkl(state, 평가 스크립트용), summary.md
"""

import argparse
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import job_log_context, setup_logging  # noqa: E402
from app.domains.documents.pipeline.nodes.chunk import chunk  # noqa: E402
from app.domains.documents.pipeline.nodes.draft_card import draft_card  # noqa: E402
from app.domains.documents.pipeline.nodes.extract import extract  # noqa: E402
from app.domains.documents.pipeline.nodes.parse import _extract_text  # noqa: E402
from app.domains.documents.pipeline.nodes.verify import verify  # noqa: E402
from app.domains.documents.pipeline.observe import finish_job_usage, start_job_usage  # noqa: E402

CARD_FIELDS = (
    "title",
    "summary",
    "context",
    "problem",
    "cause",
    "approach",
    "rationale",
    "outcome",
    "lesson",
)


def run(folder: Path) -> tuple[dict, dict, float]:
    files = sorted(f for f in folder.iterdir() if not f.name.startswith("."))
    documents = [
        {"id": uuid4(), "storage_bucket": "-", "storage_path": "-", "original_filename": f.name}
        for f in files
    ]
    state = {
        "job_id": uuid4(),
        "documents": documents,
        "parsed_documents": [
            {"document_id": d["id"], "text": _extract_text(f.read_bytes(), f.name)}
            for d, f in zip(documents, files)
        ],
    }
    state |= chunk(state)
    started = time.perf_counter()
    start_job_usage(str(state["job_id"]))
    with job_log_context(state["job_id"]):
        for node in (extract, draft_card, verify):
            update = node(state)
            state |= update
            if update.get("error"):
                break
    usage = finish_job_usage(str(state["job_id"]))
    return (
        state,
        {"calls": usage.llm_calls, "input": usage.input_tokens, "output": usage.output_tokens},
        time.perf_counter() - started,
    )


def render(folder: Path, state: dict, usage: dict, seconds: float) -> list[str]:
    names = {d["id"]: d["original_filename"] for d in state["documents"]}
    chunks = {c["id"]: c for c in state["chunks"]}
    experiences, drafts = state.get("experiences", []), state.get("card_drafts", [])
    lines = [
        f"# {folder}",
        "",
        f"- 경험 {len(experiences)}개 -> 카드 후보 {len(drafts)}개, 미생성 {len(state.get('draft_skips', []))}개",
        f"- LLM {usage['calls']}회, 입력 {usage['input']:,} / 출력 {usage['output']:,} 토큰, {seconds:.0f}초",
        f"- 오류: {state.get('error') or '없음'}",
    ]
    lines += [f"- 경고: {w}" for w in state.get("warnings", [])]
    skips = [f"- {s}" for s in state.get("draft_skips", [])] or ["- 없음"]
    lines += ["", "## 후보를 만들지 않은 경험", "", *skips]

    lines += ["", "## 추출된 경험", ""]
    for i, e in enumerate(experiences, 1):
        docs = sorted({names[chunks[v.chunk_id]["document_id"]] for v in e.evidences})
        lines.append(f"{i}. **{e.title}** — {', '.join(docs)}")
        for v in e.evidences:
            lines.append(f"   - [{v.kind}] {v.statement}")

    verified = state.get("verified_cards", [])
    lines += ["", "## 검증 결과", ""]
    for n, c in enumerate(verified, 1):
        v = c.verification
        lines.append(
            f"{n}. **{v.verdict} / {v.status}** {c.draft.title} (비운 필드: {v.cleared_fields or '없음'})"
        )
        lines += [f"   - {r}" for r in v.reasons]

    for n, d in enumerate(drafts, 1):
        lines += ["", f"## 카드 후보 {n}. {d.title}", ""]
        for field in CARD_FIELDS:
            value = getattr(d, field)
            if not value:
                lines.append(f"- **{field}**: (비움)")
                continue
            refs = ", ".join(
                f"{d.evidences[i].kind or '머리말'}@{names[d.evidences[i].document_id]}"
                for i in d.field_sources.get(field, [])
            )
            lines.append(f"- **{field}**: {value}  _[{refs}]_")
        lines.append(
            f"- **선택 필드**: 기술 {d.technologies} / 키워드 {d.keywords} / 프로젝트 {d.project_name}"
            f" / 부서 {d.department} / 시점 {d.project_date}"
        )
        lines += ["", "근거:"]
        for i, ev in enumerate(d.evidences):
            lines.append(
                f"- [{i}] {ev.kind or '머리말'} | {names[ev.document_id]} · {ev.location} | {ev.excerpt}"
            )
    return lines + [""]


def _verdicts(state: dict) -> str:
    verdicts = [c.verification.verdict for c in state.get("verified_cards", [])]
    return "/".join(str(verdicts.count(k)) for k in ("passed", "held", "excluded", "incomplete"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("projects", nargs="+")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    setup_logging()

    out = Path(args.out or f"runs/dry/{datetime.now():%Y%m%d-%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)
    summary = [
        "| 프로젝트 | 경험 | 후보 | 미생성 | 검증(통과/보류/제외/미완료) | 입력 토큰 | 출력 토큰 | 초 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for project in args.projects:
        folder = Path(project).expanduser()
        name = f"{folder.parent.parent.name}-{folder.name}".replace(" ", "")
        state, usage, seconds = run(folder)
        (out / f"{name}.pkl").write_bytes(pickle.dumps(state))
        (out / f"{name}.md").write_text(
            "\n".join(render(folder, state, usage, seconds)), encoding="utf-8"
        )
        summary.append(
            f"| {name} | {len(state.get('experiences', []))} | {len(state.get('card_drafts', []))} | "
            f"{len(state.get('draft_skips', []))} | {_verdicts(state)} | {usage['input']:,} | {usage['output']:,} | {seconds:.0f} |"
        )
        print(summary[-1], flush=True)
    (out / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    print(f"결과: {out}")


if __name__ == "__main__":
    main()
