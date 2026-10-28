# 노드: 업로드된 파일(pdf/docx/md/txt/xlsx)에서 텍스트를 추출한다.
# txt는 구조가 없는 순수 텍스트라 docling 없이 그냥 디코딩하고, 나머지는 docling으로 변환한다.
import asyncio
from io import BytesIO

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.document_converter import DocumentConverter

from app.domains.documents import repository
from app.domains.documents.pipeline.state import IngestionState, ParsedDocument

_DOCLING_FORMATS = {
    ".pdf": InputFormat.PDF,
    ".docx": InputFormat.DOCX,
    ".md": InputFormat.MD,
    ".xlsx": InputFormat.XLSX,
}

# 모델 로딩 비용이 있어서 노드 호출마다 새로 만들지 않고 모듈 전역에서 하나만 재사용한다
_converter = DocumentConverter(allowed_formats=list(_DOCLING_FORMATS.values()))


def _extract_text(content: bytes, filename: str) -> str:
    extension = filename[filename.rfind(".") :].lower() if "." in filename else ""
    if extension == ".txt":
        return content.decode("utf-8", errors="replace")

    stream = DocumentStream(name=filename, stream=BytesIO(content))
    result = _converter.convert(stream)
    return result.document.export_to_markdown()


async def parse(state: IngestionState) -> dict:
    parsed_documents: list[ParsedDocument] = []
    for document in state["documents"]:
        filename = document["original_filename"]
        try:
            content = repository.download_file(document["storage_path"])
            # docling 변환은 동기 CPU 작업이라 이벤트 루프를 막지 않도록 스레드에서 돌린다
            text = await asyncio.to_thread(_extract_text, content, filename)
        except Exception as e:
            # 문서 하나라도 실패하면 job 전체를 중단한다. 어느 문서인지 바로 알 수 있게 파일명을 남긴다
            return {"error": f"'{filename}' 파싱 실패: {e}"}
        parsed_documents.append({"document_id": document["id"], "text": text})
    return {"parsed_documents": parsed_documents}
