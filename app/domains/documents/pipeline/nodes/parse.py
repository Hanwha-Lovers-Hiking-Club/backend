# 노드: 업로드된 파일(pdf/docx/md/txt/xlsx)에서 텍스트를 추출한다.
# txt는 구조가 없는 순수 텍스트라 docling 없이 그냥 디코딩하고, 나머지는 docling으로 변환한다.
import asyncio
from io import BytesIO

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.document_converter import DocumentConverter

from app.domains.documents import repository
from app.domains.documents.pipeline.retry import retry_async
from app.domains.documents.pipeline.state import IngestionState, ParsedDocument

_DOCLING_FORMATS = {
    ".pdf": InputFormat.PDF,
    ".docx": InputFormat.DOCX,
    ".md": InputFormat.MD,
    ".xlsx": InputFormat.XLSX,
}

# 모델 로딩 비용이 있어서 노드 호출마다 새로 만들지 않고 모듈 전역에서 하나만 재사용한다
_converter = DocumentConverter(allowed_formats=list(_DOCLING_FORMATS.values()))


# 다운로드 재시도는 retry.py(최초 1회 + 재시도 2회, 1초/2초 지수 백오프 + 지터)를 쓴다.
# 변환(docling) 실패는 파일 자체 문제인 경우가 많아 재시도하지 않는다.
# ponytail: 모든 다운로드 예외를 재시도한다(파일 없음 같은 영구 오류도 ~3초 낭비).
# 거슬리면 retry_on을 예외 종류로 좁힌다
async def _download_with_retry(path: str) -> bytes:
    # supabase-py 다운로드는 동기 호출이라 스레드에서 돌려 이벤트 루프를 막지 않는다
    return await retry_async(
        lambda: asyncio.to_thread(repository.download_file, path), name="문서 다운로드"
    )


def _decode_text(content: bytes) -> str:
    # utf-8-sig는 윈도우 메모장이 붙이는 BOM을 걷어낸다. 한국 환경 txt는 cp949도 흔해서
    # 그다음에 시도한다. 둘 다 아니면 깨진 글자로라도 진행한다
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            pass
    return content.decode("utf-8", errors="replace")


def _extract_text(content: bytes, filename: str) -> str:
    extension = filename[filename.rfind(".") :].lower() if "." in filename else ""
    if extension == ".txt":
        return _decode_text(content)

    stream = DocumentStream(name=filename, stream=BytesIO(content))
    result = _converter.convert(stream)
    return result.document.export_to_markdown()


async def parse(state: IngestionState) -> dict:
    parsed_documents: list[ParsedDocument] = []
    for document in state["documents"]:
        filename = document["original_filename"]
        try:
            content = await _download_with_retry(document["storage_path"])
            # docling 변환은 동기 CPU 작업이라 이벤트 루프를 막지 않도록 스레드에서 돌린다
            text = await asyncio.to_thread(_extract_text, content, filename)
        except Exception as e:
            # 문서 하나라도 실패하면 job 전체를 중단한다. 어느 문서인지 바로 알 수 있게 파일명을 남긴다
            return {"error": f"'{filename}' 파싱 실패: {e}"}
        parsed_documents.append({"document_id": document["id"], "text": text})
    return {"parsed_documents": parsed_documents}
