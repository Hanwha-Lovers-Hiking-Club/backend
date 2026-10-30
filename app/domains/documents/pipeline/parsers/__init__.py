# 원래 파일명을 기준으로 형식별 파서를 선택한다. GitHub 전용 문서 구조는 가정하지 않는다.

import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml.etree import XMLSyntaxError
from openpyxl.utils.exceptions import InvalidFileException
from pdfplumber.utils.exceptions import PdfminerException

from app.domains.documents.pipeline.document import DocumentSource, ParsedDocument, ParseOptions
from app.domains.documents.pipeline.parsers.errors import ParseError

SUPPORTED_FORMATS = frozenset({".pdf", ".md", ".docx", ".xlsx", ".txt"})


def parse_document(source: DocumentSource, options: ParseOptions | None = None) -> ParsedDocument:
    options = options or ParseOptions()
    extension = Path(source.original_filename).suffix.lower()
    if extension not in SUPPORTED_FORMATS:
        raise ParseError("UNSUPPORTED_FORMAT", f"Unsupported document format: {extension}")
    try:
        with source.path.open("rb") as stream:
            data = stream.read(options.max_bytes + 1)
    except OSError as exc:
        raise ParseError("SOURCE_UNAVAILABLE", "Cannot read the local source file") from exc
    if not data:
        raise ParseError("EMPTY_FILE", "The document is empty")
    if len(data) > options.max_bytes:
        raise ParseError("FILE_TOO_LARGE", "The document exceeds the parser byte limit")
    try:
        if extension == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ParseError("INVALID_FILE", "A PDF signature was not found")
            from app.domains.documents.pipeline.parsers.pdf import parse_pdf

            blocks, metadata, warnings = parse_pdf(data)
        elif extension in {".docx", ".xlsx"}:
            marker = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
            with ZipFile(BytesIO(data)) as archive:
                if sum(item.file_size for item in archive.infolist()) > options.max_expanded_bytes:
                    raise ParseError("ARCHIVE_TOO_LARGE", "Expanded Office document exceeds limit")
                if marker not in archive.namelist():
                    raise ParseError("INVALID_FILE", f"Missing {marker}")
            from app.domains.documents.pipeline.parsers.office import parse_docx, parse_xlsx

            blocks, metadata, warnings = (
                parse_docx(data) if extension == ".docx" else parse_xlsx(data, options)
            )
        else:
            from app.domains.documents.pipeline.parsers.text import parse_text

            blocks, metadata, warnings = parse_text(data, markdown=extension == ".md")
    except ParseError:
        raise
    except (
        BadZipFile,
        ValueError,
        KeyError,
        XMLSyntaxError,
        InvalidFileException,
        PdfminerException,
    ) as exc:
        raise ParseError("INVALID_FILE", f"Cannot parse {extension}: {type(exc).__name__}") from exc
    if not blocks:
        raise ParseError("EMPTY_CONTENT", "No readable document content was found")
    return ParsedDocument(
        document_id=source.document_id,
        original_filename=source.original_filename,
        blocks=blocks,
        metadata={**metadata, "format": extension[1:], "sha256": hashlib.sha256(data).hexdigest()},
        warnings=warnings,
    )
