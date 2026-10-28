import re

from markdown_it import MarkdownIt

from app.domains.documents.pipeline.document import ParsedBlock
from app.domains.documents.pipeline.parsers.errors import ParseError

LOG_START = re.compile(r"^(?:Traceback|\d{4}-\d\d-\d\d|\[?(?:ERROR|WARN|INFO|DEBUG|FATAL)\b)")


def decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text or any(ord(c) < 32 and c not in "\t\n\r" for c in text):
            raise ParseError("INVALID_TEXT", "Binary control characters in text document")
        return text, encoding
    raise ParseError("UNSUPPORTED_ENCODING", "Expected UTF-8, UTF-8-BOM or CP949 text")


def parse_text(data: bytes, *, markdown: bool):
    text, encoding = decode_text(data)
    lines = text.splitlines(keepends=True)
    if not markdown:
        return _plain(lines), {"encoding": encoding}, []
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    blocks = []
    headings = []
    for index, token in enumerate(tokens):
        if token.level != 0 or token.map is None or token.nesting == -1:
            continue
        start, end = token.map
        raw = "".join(lines[start:end])
        if not raw.strip():
            continue
        kind = "paragraph"
        metadata = {"line_start": start + 1, "line_end": end}
        if token.type == "heading_open":
            level = int(token.tag[1:])
            label = tokens[index + 1].content
            headings = [entry for entry in headings if entry[0] < level]
            headings.append((level, label))
            kind = "heading"
            metadata["heading_level"] = level
        elif token.type in {"fence", "code_block"}:
            kind = "log" if LOG_START.match(token.content.lstrip()) else "code"
            metadata["language"] = token.info
        elif token.type == "table_open":
            kind = "table"
            rows, current = [], None
            for child in tokens[index + 1 :]:
                if child.type == "table_close":
                    break
                if child.type == "tr_open":
                    current = []
                elif child.type == "inline" and current is not None:
                    current.append(child.content)
                elif child.type == "tr_close":
                    rows.append(current)
            metadata["rows"] = rows
        elif token.type in {"bullet_list_open", "ordered_list_open"}:
            kind = "list"
        blocks.append(
            ParsedBlock(
                block_type=kind,
                text=raw,
                location=f"lines {start + 1}-{end}",
                section_path=[entry[1] for entry in headings],
                metadata=metadata,
            )
        )
    return blocks, {"encoding": encoding}, []


def _plain(lines):
    blocks = []
    current = []
    start = 1
    headings = []

    def flush():
        while current and not current[-1].strip():
            current.pop()
        if not current:
            return
        raw = "".join(current)
        kind = "log" if LOG_START.match(raw.lstrip()) else "paragraph"
        if kind != "log" and all(line[:1].isspace() for line in current if line.strip()):
            kind = "code"
        blocks.append(
            ParsedBlock(
                block_type=kind,
                text=raw,
                location=f"lines {start}-{start + len(current) - 1}",
                section_path=list(headings),
                metadata={"line_start": start, "line_end": start + len(current) - 1},
            )
        )
        current.clear()

    for number, line in enumerate(lines, 1):
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading:
            flush()
            headings = [heading.group(1)]
            blocks.append(
                ParsedBlock(
                    block_type="heading",
                    text=line,
                    location=f"lines {number}-{number}",
                    section_path=list(headings),
                    metadata={"line_start": number, "line_end": number},
                )
            )
            continue
        if not line.strip():
            if current and LOG_START.match(current[0].lstrip()):
                current.append(line)
                continue
            flush()
            continue
        if (
            current
            and not current[-1].strip()
            and LOG_START.match(current[0].lstrip())
            and not line[:1].isspace()
            and not LOG_START.match(line)
            and not re.match(r"^\w*(?:Error|Exception):", line)
        ):
            flush()
        if not current:
            start = number
        current.append(line)
    flush()
    return blocks
