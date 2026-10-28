from io import BytesIO
from statistics import median

import pdfplumber

from app.domains.documents.pipeline.document import ParsedBlock
from app.domains.documents.pipeline.parsers.errors import ParseError
from app.domains.documents.pipeline.parsers.text import LOG_START

MONOSPACE = ("courier", "consolas", "monospace", "menlo")


def _inside(obj, bbox):
    x = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
    y = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def parse_pdf(data: bytes):
    blocks, warnings = [], []
    headings = []
    with pdfplumber.open(BytesIO(data)) as document:
        page_count = len(document.pages)
        for page in document.pages:
            page_number = page.page_number
            if not page.chars:
                warnings.append(f"TEXT_UNAVAILABLE:p.{page_number}")
                page.close()
                continue
            if page.images:
                warnings.append(f"IMAGES_NOT_TRANSCRIBED:p.{page_number}")
            tables = page.find_tables()
            text_page = page.filter(lambda obj: not any(_inside(obj, t.bbox) for t in tables))
            sizes = [char["size"] for char in page.chars if char["text"].strip()]
            body_size = median(sizes) if sizes else 10
            items = []
            for table in tables:
                rows = [[cell or "" for cell in row] for row in table.extract()]
                text = "\n".join("\t".join(row) for row in rows)
                if text.strip():
                    items.append(
                        (
                            table.bbox[1],
                            table.bbox[0],
                            "table",
                            text,
                            {"bbox": list(table.bbox), "rows": rows},
                        )
                    )
            for line in text_page.extract_text_lines(layout=False, strip=False, return_chars=True):
                chars = [char for char in line["chars"] if char["text"].strip()]
                text = line["text"]
                if not text.strip() or not chars:
                    continue
                fonts = [char.get("fontname", "").lower() for char in chars]
                mono = all(any(name in font for name in MONOSPACE) for font in fonts)
                kind = "code" if mono else "paragraph"
                if LOG_START.match(text.lstrip()):
                    kind = "log"
                elif not mono and median(c["size"] for c in chars) > body_size * 1.25:
                    kind = "heading"
                items.append(
                    (
                        line["top"],
                        line["x0"],
                        kind,
                        text,
                        {
                            "bbox": [line["x0"], line["top"], line["x1"], line["bottom"]],
                            "lines": [
                                {
                                    "x0": line["x0"],
                                    "text": text,
                                    "char_width": median(c["width"] for c in chars),
                                }
                            ],
                        },
                    )
                )
            pending = None

            def flush():
                nonlocal pending
                if pending is None:
                    return
                top, left, kind, text, metadata = pending
                if kind in {"code", "log"}:
                    text = "\n".join(
                        " " * max(0, round((line["x0"] - left) / max(line["char_width"], 1)))
                        + line["text"]
                        for line in metadata["lines"]
                    )
                if kind == "heading":
                    headings[:] = [text.strip()]
                blocks.append(
                    ParsedBlock(
                        block_type=kind,
                        text=text,
                        location=f"p.{page_number} / bbox {left:.2f},{top:.2f}",
                        section_path=list(headings),
                        metadata={"page": page_number, **metadata},
                    )
                )
                pending = None

            for item in sorted(items, key=lambda value: (value[0], value[1])):
                top, left, kind, text, metadata = item
                if (
                    pending is not None
                    and kind == pending[2]
                    and kind in {"paragraph", "code", "log"}
                    and top - pending[4]["bbox"][3] <= body_size * 1.2
                ):
                    pending = (
                        pending[0],
                        min(left, pending[1]),
                        kind,
                        pending[3] + "\n" + text,
                        {
                            "bbox": [
                                pending[4]["bbox"][0],
                                pending[0],
                                max(metadata["bbox"][2], pending[4]["bbox"][2]),
                                metadata["bbox"][3],
                            ],
                            "lines": pending[4]["lines"] + metadata["lines"],
                        },
                    )
                else:
                    flush()
                    pending = item
            flush()
            page.close()
    if not blocks:
        raise ParseError(
            "TEXT_UNAVAILABLE", "PDF has no extractable text; OCR/vision is not configured"
        )
    return blocks, {"page_count": page_count}, warnings
