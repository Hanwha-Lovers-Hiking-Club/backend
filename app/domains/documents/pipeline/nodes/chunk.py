# 노드: 추출한 텍스트를 청크로 나눈다. 근거 표시용 위치(location) 정보를 같이 유지한다.
#
# 순서가 중요하다: 먼저 마크다운 헤딩(#, ##, ...) 구조로 "섹션" 단위로 나누고, 섹션이
# CHUNK_SIZE_TOKENS보다 길 때만 그 섹션 안에서 추가로 쪼갠다. 이렇게 해야 청크가 섹션 경계를
# 절대 넘지 않아서(= 한 청크가 두 섹션에 걸치는 일이 없어서) location이 항상 정확하고,
# 상위/하위 헤딩을 "2장 · 답변 근거와 지식 연결" 형태의 경로로도 만들 수 있다.
# 헤딩이 없는 문서(txt 등)는 섹션 1개(빈 location)로 취급된다.
import re

import semchunk
import tiktoken

from app.domains.documents.pipeline.state import Chunk, IngestionState

# 섹션이 이보다 길 때만 그 안에서 추가로 쪼갠다 (고정 분할 기준이 아니라 상한선)
CHUNK_SIZE_TOKENS = 500

# 토큰 수 기준은 llm.py의 실제 모델과 무관하게 OpenAI 계열에서 널리 쓰는 인코딩으로 고정한다.
_tokenizer = tiktoken.get_encoding("cl100k_base")
_chunker = semchunk.chunkerify(_tokenizer, chunk_size=CHUNK_SIZE_TOKENS)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_FENCE_RE = re.compile(r"^(```|~~~)")


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


def chunk(state: IngestionState) -> dict:
    chunks: list[Chunk] = []
    for document in state["parsed_documents"]:
        index = 0
        for location, section_text in _split_sections(document["text"]):
            for piece in _chunker(section_text):
                chunks.append(
                    {
                        "id": f"{document['document_id']}:{index}",
                        "document_id": document["document_id"],
                        "text": piece,
                        "location": location,
                    }
                )
                index += 1
    return {"chunks": chunks}
