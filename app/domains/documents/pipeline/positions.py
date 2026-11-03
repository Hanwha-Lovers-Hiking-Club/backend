# 원문 위치 목록과, 근거 인용문을 그 위치에서 찾는 함수.
#
# 청크는 마크다운 기준으로 나뉘어 location이 헤딩 경로뿐이다(PDF 페이지, XLSX 셀 등은 마크다운에서
# 사라진다).
# 그래서 parse가 변환 결과에서 "항목 텍스트 + 위치" 목록을 따로 만들고, draft_card가 카드 근거를
# 만들 때
# 인용문을 이 목록에서 찾아 정밀한 위치를 붙인다. 청크 경계는 바꾸지 않는다(검증한 실험 결과를
# 유지).
#
# 형식별 단위와 표시:
#   PDF  문단·표의 행   "p.2 · 2.3 조치와 결과"
#   DOCX 단락·표의 행   "3. 결정 사항 · 표 2 · 3행" / "1. 개요 · 단락 7" (DOCX에는 고정된 쪽이 없다)
#   XLSX 표의 행        "시트 동시성테스트 · A5:H5"
#   TXT  줄             "12행"
#   MD   줄             "2. 제약 · 18행"
import re

from app.domains.documents.pipeline.nodes.chunk import _FENCE_RE, _HEADING_RE
from app.domains.documents.pipeline.state import Position

_NON_TEXT = re.compile(r"[\W_]+")
# extract의 인용 검사와 같은 규칙으로 인용을 조각으로 나눈다("..." 생략, 이어 붙인 문장·표 칸)
_QUOTE_SPLIT = re.compile(r"\.\.\.|…|\[\.\.\.\]|(?<=[.다음함됨임])\s+|\n|\s\|\s")
_MIN_PIECE_CHARS = 6
# 인용 조각이 항목 경계에 걸칠 때 이어 붙여 볼 최대 항목 수
_MAX_WINDOW = 3
# 인용 전체를 한 번에 찾을 때 이어 붙여 볼 최대 항목 수(표 여러 행을 한꺼번에 인용하는 경우)
_MAX_WHOLE_WINDOW = 10


def _plain(text: str) -> str:
    return _NON_TEXT.sub("", text)


def _join_heading(stack: list[tuple[int, str]]) -> str:
    return " · ".join(title for _, title in stack)


def _push(stack: list[tuple[int, str]], level: int, title: str) -> None:
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, title))


def text_positions(text: str, markdown: bool) -> list[Position]:
    # TXT/MD: 원문을 직접 읽으므로 줄 번호가 정확하다. MD는 chunk와 같은 규칙으로 헤딩 경로를 붙인다
    positions: list[Position] = []
    stack: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if markdown and _FENCE_RE.match(line):
            in_fence = not in_fence
        elif markdown and not in_fence and (m := _HEADING_RE.match(line)):
            _push(stack, len(m.group(1)), m.group(2).strip())
        if not line.strip():
            continue
        heading = _join_heading(stack)
        location = f"{heading} · {number}행" if heading else f"{number}행"
        positions.append({"text": line, "location": location, "heading": heading})
    return positions


def _column(index: int) -> str:
    # 0 -> A, 25 -> Z, 26 -> AA
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _sheet_of(item, doc) -> str | None:
    node = item.parent.resolve(doc) if item.parent else None
    while node is not None:
        if getattr(node, "label", None) == "sheet":
            return node.name
        node = node.parent.resolve(doc) if getattr(node, "parent", None) else None
    return None


def _in_table_cell(item, doc) -> bool:
    # docling은 DOCX 표의 셀 글자를 rich_cell_group 아래의 텍스트 항목으로도 내보낸다
    parent = item.parent.resolve(doc) if item.parent else None
    return parent is not None and str(getattr(parent, "name", "")).startswith("rich_cell_group")


def docling_positions(doc, kind: str) -> list[Position]:
    # PDF/DOCX/XLSX: DoclingDocument의 항목 순서대로 위치를 만든다. kind는 "pdf" | "docx" | "xlsx"
    positions: list[Position] = []
    stack: list[tuple[int, str]] = []
    paragraph = table_number = 0
    for item, _level in doc.iterate_items():
        label = getattr(getattr(item, "label", ""), "value", "")
        prov = getattr(item, "prov", None) or []
        page = prov[0].page_no if prov else None
        if label == "title":
            _push(stack, 1, item.text.strip())
        elif label == "section_header":
            _push(stack, getattr(item, "level", 1) + 1, item.text.strip())
        heading = _join_heading(stack)

        if label == "table":
            table_number += 1
            sheet = _sheet_of(item, doc) if kind == "xlsx" else None
            box = prov[0].bbox.as_tuple() if prov else None
            for r, row in enumerate(item.data.grid):
                text = " | ".join(cell.text for cell in row)
                if not text.replace("|", "").strip():
                    continue
                if kind == "xlsx" and box:
                    # xlsx의 bbox는 (시작 열, 시작 행, 끝 열(제외), 끝 행(제외)) 셀 좌표다
                    left, top, right = int(box[0]), int(box[1]), int(box[2])
                    row_no = top + r + 1
                    cells = f"{_column(left)}{row_no}:{_column(right - 1)}{row_no}"
                    location = f"시트 {sheet} · {cells}" if sheet else cells
                elif kind == "pdf":
                    location = " · ".join(
                        x for x in (f"p.{page}", heading, f"표 {table_number} {r + 1}행") if x
                    )
                else:
                    location = " · ".join(
                        x for x in (heading, f"표 {table_number}", f"{r + 1}행") if x
                    )
                positions.append(
                    {
                        "text": text,
                        "location": location,
                        "heading": "" if kind == "xlsx" else heading,
                    }
                )
            continue

        text = getattr(item, "text", "")
        if not text or not text.strip() or _in_table_cell(item, doc):
            continue  # 표 셀 글자는 표의 행으로 이미 들어갔다(단락 번호에 세지 않는다)
        paragraph += 1
        if kind == "pdf":
            location = " · ".join(x for x in (f"p.{page}" if page else "", heading) if x)
        else:
            location = " · ".join(x for x in (heading, f"단락 {paragraph}") if x)
        positions.append({"text": text, "location": location, "heading": heading})
    return positions


def _pieces(quote: str) -> list[str]:
    pieces = [p for p in map(_plain, _QUOTE_SPLIT.split(quote)) if len(p) >= _MIN_PIECE_CHARS]
    whole = _plain(quote)
    return pieces or ([whole] if whole else [])


def _find(
    piece: str, plains: list[str], positions: list[Position], heading: str | None
) -> list[int]:
    # 조각이 들어 있는 항목(또는 이어 붙인 인접 항목들)의 인덱스. 여러 곳이면 근거 청크와 같은
    # 헤딩을 고른다
    for window in range(1, _MAX_WINDOW + 1):
        found = [
            list(range(i, i + window))
            for i in range(len(plains) - window + 1)
            if piece in "".join(plains[i : i + window])
        ]
        if found:
            same = [f for f in found if heading and positions[f[0]]["heading"] == heading]
            return (same or found)[0]
    return []


def locate(quote: str, positions: list[Position], heading: str | None = None) -> str | None:
    """인용문이 있는 원문 위치를 돌려준다. 여러 곳에 걸치면 "처음 ~ 끝", 못 찾으면 None."""
    pieces = _pieces(quote)
    if not pieces or not positions:
        return None
    plains = [_plain(p["text"]) for p in positions]
    # 인용 전체가 한 항목(또는 인접 항목)에 다 있는 곳을 먼저 찾는다. 조각별로 따로 찾으면 짧은 조각
    # (날짜, 숫자)이 엉뚱한 곳(다른 시트의 같은 날짜 등)에서 먼저 잡힌다
    whole = _find_all(pieces, plains, positions, heading)
    if whole:
        return _span(positions, whole)
    hits: list[int] = []
    for piece in pieces:
        found = _find(piece, plains, positions, heading)
        if not found:
            return None
        hits += found
    return _span(positions, hits)


def _find_all(pieces, plains, positions, heading) -> list[int]:
    for window in range(1, _MAX_WHOLE_WINDOW + 1):
        found = [
            list(range(i, i + window))
            for i in range(len(plains) - window + 1)
            if all(p in "".join(plains[i : i + window]) for p in pieces)
        ]
        if found:
            same = [f for f in found if heading and positions[f[0]]["heading"] == heading]
            return (same or found)[0]
    return []


def _span(positions: list[Position], hits: list[int]) -> str:
    first, last = positions[min(hits)]["location"], positions[max(hits)]["location"]
    return first if first == last else f"{first} ~ {last}"
