# ruff: noqa: E501 — 리포트 줄을 만드는 f-string이 길다(개발용 스크립트)
"""근거 위치 평가: dry-run 결과(state)의 카드 근거 인용문으로 위치를 다시 계산하고, docling과 독립된 도구로
그 위치에 인용문이 실제로 있는지 확인한다. LLM을 부르지 않는다(문서 변환만 다시 한다).

확인 도구: PDF는 pypdfium2로 해당 쪽 텍스트, XLSX는 openpyxl로 해당 셀 범위, DOCX 표는 python-docx로 해당 행,
TXT/MD는 원문의 해당 줄. DOCX 단락은 순번 기준이 도구마다 달라 문서 전체에 있는지만 본다(약한 확인).

사용법 (레포 루트에서):
    uv run python scripts/location_eval.py runs/dry/<실행 폴더> [...] --root ~/Desktop
"""

import argparse
import pickle
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domains.documents.pipeline.nodes.parse import _convert  # noqa: E402
from app.domains.documents.pipeline.positions import _pieces, _plain, locate  # noqa: E402

_PAGES = re.compile(r"p\.(\d+)")
_CELLS = re.compile(r"시트 (.+?) · ([A-Z]+)(\d+):([A-Z]+)(\d+)")
_LINES = re.compile(r"(\d+)행$")
_TABLE = re.compile(r"표 (\d+) · (\d+)행")


def _folder(root: Path, stem: str) -> Path:
    prefix, project = stem.split("-", 1)
    for candidate in root.glob(f"*/inputs/{project}"):
        # macOS는 한글 경로를 NFD로 돌려준다
        if unicodedata.normalize("NFC", candidate.parent.parent.name).replace(
            " ", ""
        ) == unicodedata.normalize("NFC", prefix):
            return candidate
    raise FileNotFoundError(stem)


def _check(path: Path, location: str, quote: str) -> str:
    # 독립 도구로 그 위치의 원문을 읽어 인용 조각이 모두 있는지 본다. "ok" | "weak_ok" | "mismatch" | "unchecked"
    pieces = _pieces(quote)
    ext = path.suffix.lower()
    parts = location.split(" ~ ")

    def contains(text: str) -> bool:
        plain = _plain(text)
        return all(p in plain for p in pieces)

    if ext == ".pdf":
        import pypdfium2

        pages = [int(n) for part in parts for n in _PAGES.findall(part)]
        if not pages:
            return "unchecked"
        pdf = pypdfium2.PdfDocument(str(path))
        text = "".join(
            pdf[i - 1].get_textpage().get_text_range() for i in range(min(pages), max(pages) + 1)
        )
        return "ok" if contains(text) else "mismatch"
    if ext == ".xlsx":
        import openpyxl

        found = [_CELLS.search(part) for part in parts]
        if not all(found):
            return "unchecked"
        sheet = found[0].group(1)
        top, bottom = int(found[0].group(3)), int(found[-1].group(5))
        ws = openpyxl.load_workbook(path, data_only=True)[sheet]
        rows = ws.iter_rows(min_row=top, max_row=bottom, values_only=True)
        return (
            "ok"
            if contains("".join(str(v) for row in rows for v in row if v is not None))
            else "mismatch"
        )
    if ext in (".txt", ".md"):
        found = [_LINES.search(part) for part in parts]
        if not all(found):
            return "unchecked"
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        first, last = int(found[0].group(1)), int(found[-1].group(1))
        return "ok" if contains("".join(lines[first - 1 : last])) else "mismatch"
    if ext == ".docx":
        import docx

        document = docx.Document(str(path))
        tables = [_TABLE.search(part) for part in parts]
        if all(tables):
            t = int(tables[0].group(1))
            first, last = int(tables[0].group(2)), int(tables[-1].group(2))
            if t > len(document.tables):
                return "mismatch"
            rows = document.tables[t - 1].rows[first - 1 : last]
            return (
                "ok" if contains("".join(c.text for row in rows for c in row.cells)) else "mismatch"
            )
        # 단락: location의 마지막 제목을 python-docx 단락에서 찾고, 그 제목부터 다음 제목 전까지에 인용이 있는지
        heading = re.sub(r" · 단락 \d+$", "", parts[0]).split(" · ")[-1]
        paragraphs = document.paragraphs
        start = next((i for i, p in enumerate(paragraphs) if p.text.strip() == heading), None)
        if start is None:
            return "unchecked"
        section = [paragraphs[start].text]
        for p in paragraphs[start + 1 :]:
            if p.style.name.lower().startswith(("heading", "title")):
                break
            section.append(p.text)
        return "ok" if contains("".join(section)) else "mismatch"
    return "unchecked"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("run_dirs", nargs="+")
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root).expanduser()

    counts: dict[str, Counter] = {}
    problems: list[str] = []
    examples: dict[str, str] = {}
    for run_dir in args.run_dirs:
        for pkl in sorted(Path(run_dir).glob("*.pkl")):
            state = pickle.loads(pkl.read_bytes())
            folder = _folder(root, pkl.stem)
            positions = {}
            names = {str(d["id"]): d["original_filename"] for d in state["documents"]}
            for name in set(names.values()):
                positions[name] = _convert((folder / name).read_bytes(), name)[1]
            chunks = {c["id"]: c for c in state["chunks"]}
            for draft in state.get("card_drafts", []):
                for ev in draft.evidences:
                    if ev.kind is None or ev.chunk_id not in chunks:
                        continue  # 문서 머리말 근거는 따로 보지 않는다
                    name = names[str(ev.document_id)]
                    ext = Path(name).suffix.lower()
                    counter = counts.setdefault(ext, Counter())
                    location = locate(
                        ev.excerpt or "", positions[name], chunks[ev.chunk_id]["location"]
                    )
                    if location is None:
                        counter["못 찾음(청크 위치로 대체)"] += 1
                        problems.append(f"못 찾음 | {name} | {ev.excerpt[:70]!r}")
                        continue
                    result = _check(folder / name, location, ev.excerpt or "")
                    counter[result] += 1
                    examples.setdefault(ext, f"{name} → {location}")
                    if result == "mismatch":
                        problems.append(f"불일치 | {name} | {location} | {ev.excerpt[:70]!r}")

    for ext, c in sorted(counts.items()):
        total = sum(c.values())
        print(f"{ext}: 근거 {total}개 | " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
        print(f"   예: {examples.get(ext, '-')}")
    print("\n문제 사례:")
    for p in problems[:30]:
        print(" -", p)


if __name__ == "__main__":
    main()
