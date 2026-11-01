"""실제 문서 테스트 1단계: 문서 변환(parse)과 청크(chunk) 결과를 형식별로 비교한다.

LLM, kure, Supabase를 전혀 부르지 않아서 비용이 없고 DB도 건드리지 않는다.
같은 문서의 원본 md를 정답으로 두고, md/docx/pdf를 파이프라인의 parse·chunk 노드 그대로 변환해서
글자·구조(헤딩, 표, 코드블록)가 얼마나 남는지, 청크가 어떻게 나뉘는지 표로 만든다.

사용법 (레포 루트에서):
    uv run python scripts/doc_test_stage1.py --root "<데이터셋 폴더>"                   # core 샘플
    uv run python scripts/doc_test_stage1.py --root "<데이터셋 폴더>" --set edge   # 까다로운 문서
    uv run python scripts/doc_test_stage1.py --root "<데이터셋 폴더>" --formats md,docx # 형식 제한
    (--root 대신 환경변수 DOC_TEST_ROOT 도 된다)

결과는 runs/stage1-<시각>/ 에 저장된다(runs/는 .gitignore): report.md(표), report.json,
그리고 변환된 마크다운 <프로젝트>/<문서>.<형식>.md — 눈으로 확인하는 용도.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doc_test_samples import SAMPLES  # noqa: E402

from app.domains.documents.pipeline.nodes import chunk as chunk_node  # noqa: E402
from app.domains.documents.pipeline.nodes.parse import _extract_text, get_converter  # noqa: E402

FORMATS = ("md", "docx", "pdf")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_WORD_RE = re.compile(r"[가-힣A-Za-z0-9_]{3,}")


def resolve(project_dir: Path, spec: str) -> Path:
    # spec이 정확한 상대 경로면 그걸, 아니면 끝부분이 맞는 파일이 하나일 때 그걸 쓴다
    base = project_dir / "original_markdown"
    exact = base / spec
    if exact.is_file():
        return exact
    found = [p for p in base.rglob("*.md") if p.as_posix().endswith(spec)]
    if len(found) != 1:
        raise SystemExit(f"샘플 경로를 하나로 정할 수 없음: {spec} ({len(found)}개 일치) in {base}")
    return found[0]


def sibling(md_path: Path, project_dir: Path, fmt: str) -> Path:
    rel = md_path.relative_to(project_dir / "original_markdown").with_suffix("")
    folder = {"docx": "docx", "pdf": "pdf"}[fmt]
    return (project_dir / folder / rel).with_suffix(f".{fmt}")


def structure(text: str) -> tuple[int, int, int]:
    # (헤딩 수, 표 덩어리 수, 코드블록 수). 코드블록 안의 #나 |는 세지 않는다
    headings = tables = codes = 0
    in_fence = prev_table = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            if not in_fence:
                codes += 1
            in_fence, prev_table = not in_fence, False
            continue
        if in_fence:
            continue
        if _HEADING_RE.match(line):
            headings += 1
        is_table = line.lstrip().startswith("|")
        if is_table and not prev_table:
            tables += 1
        prev_table = is_table
    return headings, tables, codes


def word_recall(original: str, converted: str) -> float:
    # 원본에 있던 단어(3자 이상, 중복 제거) 중 변환 결과에도 남은 비율.
    # 글자가 얼마나 보존됐는지의 근사
    source = set(_WORD_RE.findall(original))
    return len(source & set(_WORD_RE.findall(converted))) / len(source) if source else 1.0


def chunk_stats(text: str) -> dict:
    chunks = chunk_node.chunk({"parsed_documents": [{"document_id": "doc", "text": text}]})[
        "chunks"
    ]
    tokens = sorted(chunk_node._count(c["text"]) for c in chunks)
    if not tokens:
        return {"count": 0, "median": 0, "p90": 0, "max": 0, "small": 0, "large": 0}
    return {
        "count": len(tokens),
        "median": int(statistics.median(tokens)),
        "p90": tokens[min(len(tokens) - 1, int(len(tokens) * 0.9))],
        "max": tokens[-1],
        "small": sum(t < chunk_node.MIN_SECTION_TOKENS for t in tokens),
        "large": sum(t > chunk_node.CHUNK_SIZE_TOKENS for t in tokens),
    }


def measure(label: str, original: str, converted: str, seconds: float | None) -> dict:
    headings, tables, codes = structure(converted)
    return {
        "format": label,
        "seconds": seconds,
        "chars": len(converted),
        "recall": word_recall(original, converted),
        "headings": headings,
        "tables": tables,
        "codes": codes,
        **chunk_stats(converted),
    }


def run_document(project: str, project_dir: Path, spec: str, formats: list[str], out: Path) -> dict:
    md_path = resolve(project_dir, spec)
    original = md_path.read_text(encoding="utf-8")
    slug = md_path.stem
    rows = [measure("원본 md", original, original, None)]
    for fmt in formats:
        path = md_path if fmt == "md" else sibling(md_path, project_dir, fmt)
        label = "md(원문)" if fmt == "md" else fmt
        started = time.perf_counter()
        try:
            text = _extract_text(path.read_bytes(), path.name)
        except Exception as e:
            rows.append({"format": label, "error": f"{type(e).__name__}: {str(e)[:150]}"})
            print(f"  ! {slug} {fmt}: 변환 실패 {e}", flush=True)
            continue
        seconds = time.perf_counter() - started
        (out / project).mkdir(parents=True, exist_ok=True)
        (out / project / f"{slug}.{fmt}.md").write_text(text, encoding="utf-8")
        rows.append(measure(label, original, text, seconds))
        print(f"  {slug} {fmt}: {seconds:.1f}초, {len(text):,}자", flush=True)
    return {"project": project, "doc": md_path.relative_to(project_dir).as_posix(), "rows": rows}


def render(results: list[dict], notes: dict[str, str]) -> str:
    lines = ["# 1단계 변환·청크 비교", ""]
    lines += [
        "- 글자 보존 = 원본 md의 단어(3자 이상) 중 변환 결과에 남은 비율. 헤딩/표/코드 = 개수",
        "- 청크 토큰은 cl100k 기준. 작은 = 50토큰 미만, 큰 = 500토큰 초과(상한 초과)",
        "",
    ]
    for project in dict.fromkeys(r["project"] for r in results):
        lines += [f"## {project}", ""]
        for r in (r for r in results if r["project"] == project):
            short = r["doc"].split("original_markdown/")[-1]
            lines += [f"### {short}", f"_{notes.get(project + '/' + short, '')}_", ""]
            lines += [
                "| 형식 | 변환(초) | 글자수 | 글자 보존 | 헤딩 | 표 | 코드 | 청크 "
                "| 토큰 중앙/p90/최대 | 작은 | 큰 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|",
            ]
            for row in r["rows"]:
                if "error" in row:
                    lines.append(f"| {row['format']} | 실패: {row['error']} |||||||||| ")
                    continue
                sec = f"{row['seconds']:.1f}" if row["seconds"] is not None else "-"
                lines.append(
                    f"| {row['format']} | {sec} | {row['chars']:,} | {row['recall']:.0%} | "
                    f"{row['headings']} | {row['tables']} | {row['codes']} | {row['count']} | "
                    f"{row['median']}/{row['p90']}/{row['max']} | {row['small']} | {row['large']} |"
                )
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--root", default=os.environ.get("DOC_TEST_ROOT"), help="데이터셋 폴더")
    parser.add_argument("--set", choices=["core", "edge", "all"], default="core")
    parser.add_argument("--formats", default=",".join(FORMATS), help="쉼표로 구분 (md,docx,pdf)")
    parser.add_argument("--projects", default="", help="쉼표로 구분한 프로젝트만 (기본 전체)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    if not args.root:
        raise SystemExit("--root 또는 환경변수 DOC_TEST_ROOT 가 필요하다")

    root = Path(args.root)
    formats = [f for f in args.formats.split(",") if f]
    only = {p for p in args.projects.split(",") if p}
    out = Path(args.out or Path("runs") / f"stage1-{datetime.now():%Y%m%d-%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    get_converter()  # 모델 로딩 시간이 첫 문서의 변환 시간에 섞이지 않게 먼저 불러 둔다
    print(f"변환기 준비 {time.perf_counter() - started:.1f}초", flush=True)

    sets = ["core", "edge"] if args.set == "all" else [args.set]
    results: list[dict] = []
    notes: dict[str, str] = {}
    for project, groups in SAMPLES.items():
        if only and project not in only:
            continue
        matches = list(root.glob(f"{project}-*-dataset"))
        if len(matches) != 1:
            raise SystemExit(f"프로젝트 폴더를 찾을 수 없음: {project}-*-dataset in {root}")
        print(f"[{project}]", flush=True)
        for group in sets:
            for spec, note in groups[group]:
                result = run_document(project, matches[0], spec, formats, out)
                result["group"] = group
                results.append(result)
                notes[f"{project}/{result['doc'].split('original_markdown/')[-1]}"] = (
                    f"{group}: {note}"
                )
    (out / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")
    (out / "report.md").write_text(render(results, notes), encoding="utf-8")
    print(f"\n완료: {out / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
