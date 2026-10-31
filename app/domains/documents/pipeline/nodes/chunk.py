# 노드: 추출한 텍스트를 청크로 나눈다. 근거 표시용 위치(location) 정보를 같이 유지한다.
#
# 순서가 중요하다: 먼저 마크다운 헤딩(#, ##, ...) 구조로 "섹션" 단위로 나누고, 섹션이
# CHUNK_SIZE_TOKENS보다 길 때만 그 섹션 안에서 추가로 쪼갠다. 이렇게 해야 청크가 섹션 경계를
# 절대 넘지 않아서(= 한 청크가 두 섹션에 걸치는 일이 없어서) location이 항상 정확하고,
# 상위/하위 헤딩을 "2장 · 답변 근거와 지식 연결" 형태의 경로로도 만들 수 있다.
# 헤딩이 없는 문서(txt 등)는 섹션 1개(빈 location)로 취급된다.
#
# 섹션이 MIN_SECTION_TOKENS보다 짧으면(헤딩만 있는 섹션 등) 다음 섹션 앞에 이어붙인다.
# 긴 섹션을 쪼갤 때는 표(행 단위로 자르고 조각마다 헤더 반복)와 코드블록(500토큰 이하면 통째로)을
# 중간에서 자르지 않는다. 표 행/코드블록 텍스트는 원문 그대로라
# draft_card의 excerpt 검증과 충돌하지 않는다.
import re

import semchunk
import tiktoken

from app.domains.documents.pipeline.state import Chunk, IngestionState

# 섹션이 이보다 길 때만 그 안에서 추가로 쪼갠다 (고정 분할 기준이 아니라 상한선)
CHUNK_SIZE_TOKENS = 500

# 토큰 수 기준은 llm.py의 실제 모델과 무관하게 OpenAI 계열에서 널리 쓰는 인코딩으로 고정한다.
_tokenizer = tiktoken.get_encoding("cl100k_base")
_chunker = semchunk.chunkerify(_tokenizer, chunk_size=CHUNK_SIZE_TOKENS)

# 섹션이 이보다 짧으면 다음 섹션과 합친다
# ponytail: 임시값, 실제 문서의 청크 길이 분포를 보고 조정
MIN_SECTION_TOKENS = 50

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_FENCE_RE = re.compile(r"^(```|~~~)")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _count(text: str) -> int:
    return len(_tokenizer.encode(text, disallowed_special=()))


def _split_sections(text: str) -> list[tuple[str, str]]:
    # (location, section_text) 리스트를 문서 등장 순서대로 돌려준다.
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)

    stack: list[tuple[int, str]] = []  # (헤딩 레벨, 제목) 스택. 상위 헤딩이 바뀌면 하위는 비운다
    boundaries: list[tuple[int, str]] = [(0, "")]  # (시작 위치, 그 지점부터의 location)
    in_fence = False

    for line, offset in zip(lines, offsets):
        stripped = line.rstrip("\n")
        if _FENCE_RE.match(stripped):
            in_fence = not in_fence  # 코드블록 안의 #는 헤딩으로 치지 않는다
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(stripped)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        boundaries.append((offset, " · ".join(t for _, t in stack)))

    ends = [start for start, _ in boundaries[1:]] + [len(text)]
    return [
        (location, text[start:end])
        for (start, location), end in zip(boundaries, ends)
        if text[start:end].strip()
    ]


def _merge_small_sections(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    # 짧은 섹션은 다음 섹션 앞에 붙이고 location은 뒤쪽(합쳐진 쪽)을 쓴다.
    # 헤딩 텍스트는 본문에 남는다.
    # 마지막 섹션이 짧으면 이전 섹션 뒤에 붙인다.
    merged: list[tuple[str, str]] = []
    pending, pending_location = "", ""
    for location, text in sections:
        text = pending + text
        if _count(text) < MIN_SECTION_TOKENS:
            pending, pending_location = text, location
            continue
        merged.append((location, text))
        pending = ""
    if pending:
        if merged:
            merged[-1] = (merged[-1][0], merged[-1][1] + pending)
        else:
            merged.append((pending_location, pending))
    return merged


def _split_blocks(text: str) -> list[tuple[str, list[str]]]:
    # 섹션을 ("text" | "table" | "code", 줄 목록) 블록으로 나눈다. 코드펜스 안의 `|`는 표로 안 본다
    blocks: list[tuple[str, list[str]]] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if _FENCE_RE.match(line.rstrip("\n")):
            if in_fence:
                blocks[-1][1].append(line)
            else:
                blocks.append(("code", [line]))
            in_fence = not in_fence
            continue
        if in_fence:
            blocks[-1][1].append(line)
            continue
        kind = "table" if line.lstrip().startswith("|") else "text"
        if blocks and blocks[-1][0] == kind:
            blocks[-1][1].append(line)
        else:
            blocks.append((kind, [line]))
    return blocks


def _split_table(lines: list[str]) -> list[str]:
    # 헤더(첫 두 줄)+구분선이 없는 표는 표로 못 알아봐서 일반 텍스트처럼 자른다
    if len(lines) < 3 or not _TABLE_SEPARATOR_RE.match(lines[1]):
        return _chunker("".join(lines))
    header = "".join(lines[:2])
    budget = CHUNK_SIZE_TOKENS - _count(header)
    pieces: list[str] = []
    rows: list[str] = []
    used = 0
    for row in lines[2:]:
        # ponytail: 행 토큰을 따로 세어 더한다(근사).
        # 행 하나가 budget을 넘어도 쪼개지 않고 그대로 둔다
        row_tokens = _count(row)
        if rows and used + row_tokens > budget:
            pieces.append(header + "".join(rows))
            rows, used = [], 0
        rows.append(row)
        used += row_tokens
    pieces.append(header + "".join(rows))
    return pieces


def _split_section(text: str) -> list[str]:
    if _count(text) <= CHUNK_SIZE_TOKENS:
        return [text]
    pieces: list[str] = []
    for kind, lines in _split_blocks(text):
        block = "".join(lines)
        if kind == "table":
            pieces.extend(_split_table(lines))
        elif kind == "code" and _count(block) <= CHUNK_SIZE_TOKENS:
            pieces.append(block)
        else:
            pieces.extend(_chunker(block))
    return pieces


def chunk(state: IngestionState) -> dict:
    chunks: list[Chunk] = []
    for document in state["parsed_documents"]:
        index = 0
        sections = _merge_small_sections(_split_sections(document["text"]))
        for location, section_text in sections:
            for piece in _split_section(section_text):
                chunks.append(
                    {
                        "id": f"{document['document_id']}:{index}",
                        "document_id": document["document_id"],
                        "index": index,
                        "text": piece,
                        "location": location,
                    }
                )
                index += 1
    return {"chunks": chunks}
