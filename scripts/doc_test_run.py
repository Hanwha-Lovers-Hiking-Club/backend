"""실제 문서 테스트 2~3단계: 문서를 Storage/DB에 올리고 파이프라인 전체를 실제로 돌린다.

주의: 실제 Supabase(Storage, documents, 카드, 청크)에 쓰고 OpenAI와 kure를 호출한다. 만든 데이터는
지우지 않고 남기며, 지울 때 쓸 ID를 runs/ 아래 run.json에 모두 기록한다(지우는 건 사람이 한다).
프로젝트를 섞지 않으려고 한 번 실행 = 프로젝트 하나 = job 하나다.

사용법 (레포 루트에서):
    uv run python scripts/doc_test_run.py --root "<데이터셋 폴더>" --project vigilantis \\
        --docs "docs/adr/0004-rollback-runbook-whitelist-registration.md" --format md
    (--docs를 생략하면 그 프로젝트의 core 샘플 전부. --format은 md|docx|pdf)

결과는 runs/stage2-<프로젝트>-<형식>-<시각>/ 에 저장된다:
    run.log   파이프라인 로그(노드별 시간, LLM/임베딩 호출·토큰, 경고)
    run.json  job/문서/카드/청크 ID와 최종 결과 (지울 때 기준)
    cards.md  원문과 대조하기 위한 보고서: 단계별 깔때기, 카드 필드, 근거 발췌문의 원문 일치 여부
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doc_test_samples import SAMPLES  # noqa: E402
from doc_test_stage1 import resolve, sibling  # noqa: E402

from app.core.logging import _AppFormatter, setup_logging  # noqa: E402
from app.db import supabase  # noqa: E402
from app.domains.documents import repository  # noqa: E402
from app.domains.documents.pipeline.graph import build_graph  # noqa: E402
from app.domains.documents.pipeline.nodes.parse import get_converter  # noqa: E402
from app.domains.documents.service import ALLOWED_EXTENSIONS  # noqa: E402
from app.domains.knowledge_cards.service import CARD_SELECT  # noqa: E402

CARD_FIELDS = [
    "summary",
    "context",
    "problem",
    "cause",
    "approach",
    "rationale",
    "outcome",
    "lesson",
    "technologies",
    "keywords",
    "project_name",
    "department",
    "project_date",
]


def squash(text: str) -> str:
    # 공백 차이(줄바꿈, 들여쓰기)를 무시하고 비교하려고 모든 공백을 한 칸으로 줄인다
    return re.sub(r"\s+", " ", text).strip()


def upload_documents(job_id, paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        extension = path.suffix.lower()
        content = path.read_bytes()
        storage_path = f"{job_id}/{uuid4()}{extension}"
        repository.upload_file(storage_path, content, ALLOWED_EXTENSIONS[extension])
        rows.append(
            {
                "title": path.stem.strip() or path.name,
                "original_filename": path.name,
                "size_bytes": len(content),
                "storage_bucket": os.environ.get("SUPABASE_STORAGE_BUCKET", "documents"),
                "storage_path": storage_path,
            }
        )
    ids = repository.insert_documents(rows)
    return [{**row, "id": document_id} for document_id, row in zip(ids, rows, strict=True)]


def fetch_results(document_ids: list[str]) -> dict:
    links = (
        supabase.table("card_documents")
        .select("card_id")
        .in_("document_id", document_ids)
        .execute()
        .data
    )
    card_ids = sorted({row["card_id"] for row in links})
    cards = []
    if card_ids:
        cards = supabase.table("knowledge_cards").select(CARD_SELECT).in_("id", card_ids).execute()
        cards = cards.data
    chunk_counts = {
        document_id: supabase.table("document_chunks")
        .select("id", count="exact")
        .eq("document_id", document_id)
        .execute()
        .count
        for document_id in document_ids
    }
    return {"card_ids": card_ids, "cards": cards, "chunk_counts": chunk_counts}


def render_report(final: dict, results: dict, documents: list[dict], seconds: float) -> str:
    parsed = {str(p["document_id"]): p["text"] for p in final.get("parsed_documents", [])}
    chunks = final.get("chunks", [])
    elements = final.get("extracted_elements", [])
    drafts = final.get("card_drafts", [])
    verified = final.get("verified_cards", [])
    verdicts = [v.verification.verdict for v in verified]
    judged = {v.draft.title: v.verification for v in verified}

    lines = ["# 실제 문서 테스트 결과 (원문과 대조용)", ""]
    lines += ["## 문서", ""]
    for d in documents:
        lines.append(f"- `{d['original_filename']}` (id `{d['id']}`)")
    lines += ["", "## 단계별 깔때기", ""]
    lines += [
        f"- 소요: {seconds:.1f}초 | 오류: {final.get('error') or '없음'}",
        f"- 청크 {len(chunks)}개 → 경험 {len(elements)}개 → 카드 초안 {len(drafts)}개",
        f"- 검증: pass {verdicts.count('pass')} / rewrite {verdicts.count('rewrite')} / "
        f"discard {verdicts.count('discard')}",
        f"- 임베딩한 카드 {len(final.get('embedded_cards', []))}개 → "
        f"저장 {final.get('saved_card_count', 0)}개 (DB에서 조회한 카드 {len(results['cards'])}개)",
        f"- DB 청크 수: {sum(results['chunk_counts'].values())}개",
    ]
    for warning in final.get("warnings", []):
        lines.append(f"- 경고: {warning}")

    lines += ["", "## 추출된 경험 (extract 결과)", ""]
    location = {c["id"]: c["location"] for c in chunks}
    for i, e in enumerate(elements, 1):
        where = ", ".join(
            sorted({location.get(c, "?") or "(헤딩 없음)" for c in e.source_chunk_ids})
        )
        lines.append(f"{i}. **문제**: {e.problem} / **해결**: {e.approach} / _근거 위치: {where}_")

    lines += ["", "## 검증에서 pass가 아닌 카드 (rewrite/discard)", ""]
    skipped = [v for v in verified if v.verification.verdict != "pass"]
    for v in skipped or []:
        lines.append(f"- [{v.verification.verdict}] {v.draft.title} — {v.verification.reason}")
    if not skipped:
        lines.append("- 없음")

    lines += ["", "## 저장된 카드", ""]
    for n, card in enumerate(results["cards"], 1):
        lines += [f"### 카드 {n}. {card['title']}", f"`{card['id']}`", ""]
        for field in CARD_FIELDS:
            value = card.get(field)
            if value in (None, "", []):
                continue
            lines.append(f"- **{field}**: {value}")
        verdict = judged.get(card["title"])
        if verdict:
            lines.append(f"- **검증 판정**: {verdict.verdict} — {verdict.reason}")
        lines += ["", "근거 (excerpt가 원문에 그대로 있는지 자동 확인):", ""]
        for ev in card.get("card_documents", []):
            source = squash(parsed.get(ev["document_id"], ""))
            excerpt = ev["excerpt"] or ""
            found = "✓ 원문에 있음" if squash(excerpt) in source else "✗ 원문에 없음"
            lines += [f"- {found} | 위치: {ev['location']}", f"  > {excerpt}"]
        lines.append("")
    return "\n".join(lines)


async def run_graph(job_id, documents: list[dict]) -> dict:
    state = {
        "job_id": job_id,
        "documents": [
            {
                "id": d["id"],
                "storage_bucket": d["storage_bucket"],
                "storage_path": d["storage_path"],
                "original_filename": d["original_filename"],
            }
            for d in documents
        ],
    }
    return await build_graph().ainvoke(state)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--root", default=os.environ.get("DOC_TEST_ROOT"))
    parser.add_argument("--project", required=True, choices=sorted(SAMPLES))
    parser.add_argument(
        "--docs", default="", help="쉼표로 구분한 문서 경로(끝부분). 생략하면 core 전부"
    )
    parser.add_argument("--format", choices=["md", "docx", "pdf"], default="md")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    if not args.root:
        raise SystemExit("--root 또는 환경변수 DOC_TEST_ROOT 가 필요하다")

    project_dir = next(iter(Path(args.root).glob(f"{args.project}-*-dataset")), None)
    if project_dir is None:
        raise SystemExit(f"프로젝트 폴더를 찾을 수 없음: {args.project}")
    specs = [s for s in args.docs.split(",") if s] or [s for s, _ in SAMPLES[args.project]["core"]]
    md_paths = [resolve(project_dir, spec) for spec in specs]
    paths = [p if args.format == "md" else sibling(p, project_dir, args.format) for p in md_paths]

    out = Path(
        args.out
        or Path("runs") / f"stage2-{args.project}-{args.format}-{datetime.now():%Y%m%d-%H%M%S}"
    )
    out.mkdir(parents=True, exist_ok=True)
    setup_logging()
    file_handler = logging.FileHandler(out / "run.log", encoding="utf-8")
    file_handler.setFormatter(_AppFormatter())
    logging.getLogger("app").addHandler(file_handler)

    print(f"프로젝트 {args.project} | 형식 {args.format} | 문서 {len(paths)}개", flush=True)
    for p in paths:
        print(f"  - {p.name}", flush=True)
    if args.format in ("docx", "pdf"):
        started = time.perf_counter()
        get_converter()  # 모델 로딩 시간이 job 시간에 섞이지 않게 먼저 불러 둔다
        print(f"변환기 준비 {time.perf_counter() - started:.1f}초", flush=True)

    job_id = uuid4()
    documents = upload_documents(job_id, paths)
    print(f"업로드 완료: job {job_id}, 문서 id {[d['id'] for d in documents]}", flush=True)

    started = time.perf_counter()
    final = asyncio.run(run_graph(job_id, documents))
    seconds = time.perf_counter() - started

    document_ids = [d["id"] for d in documents]
    results = fetch_results(document_ids)
    (out / "run.json").write_text(
        json.dumps(
            {
                "job_id": str(job_id),
                "project": args.project,
                "format": args.format,
                "seconds": round(seconds, 1),
                "error": final.get("error"),
                "warnings": final.get("warnings", []),
                "saved_card_count": final.get("saved_card_count", 0),
                "documents": documents,
                "card_ids": results["card_ids"],
                "chunk_counts": results["chunk_counts"],
                "cleanup_hint": "card_ids의 knowledge_cards 삭제(근거는 cascade) -> documents 삭제"
                "(청크는 cascade) -> storage_path 파일 삭제",
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    (out / "cards.md").write_text(
        render_report(final, results, documents, seconds), encoding="utf-8"
    )
    print(f"\n완료 ({seconds:.1f}초): {out / 'cards.md'}", flush=True)


if __name__ == "__main__":
    main()
