# 노드: 업로드된 파일(pdf/docx/md/txt/xlsx)에서 텍스트를 추출한다.
# txt와 md는 이미 텍스트(md는 마크다운 그 자체)라 docling 없이 그냥 디코딩하고,
# 나머지는 docling으로 변환한다.
# md를 docling에 넣으면 굵은 글씨 안의 인라인 코드가 문단 중간의 코드블록으로 바뀌어
# 짝이 안 맞는 펜스가 생기고, 청크 노드가 그 뒤의 헤딩을 전부 잃는 문제가 실제 문서
# (vigilantis ADR-0010)에서 확인됐다.
import asyncio
import threading
from io import BytesIO

from app.core.logging import summarize_error
from app.domains.documents import repository
from app.domains.documents.pipeline.positions import docling_positions, text_positions
from app.domains.documents.pipeline.retry import retry_async
from app.domains.documents.pipeline.state import IngestionState, ParsedDocument, Position

# docling은 import만으로 torch까지 불러와 20초 가까이 걸리고, PDF 모델 로딩도 따로 걸린다.
# 서버 시작(과 --reload)을 붙잡지 않도록 import는 이 함수 안에서 하고, 서버가 켜질 때
# 백그라운드 스레드가 미리 불러 둔다(documents/service.start_converter_warm_up).
# 미리 불러오는 중에 업로드가 들어와도 변환기를 두 번 만들지 않게 잠그고,
# 만드는 쪽이 끝나길 기다린다.
# 변환기는 모델 로딩 비용이 있어서 노드 호출마다 새로 만들지 않고 하나만 재사용한다.
# 로딩이 실패하면 결과를 저장하지 않으므로 다음 호출에서 다시 시도한다
_TEXT_EXTENSIONS = {".txt", ".md"}
# 이보다 짧은 추출 결과는 실패로 본다(스캔 이미지뿐인 PDF처럼 오류 없이 빈 텍스트가 나오는 경우).
# ponytail: 공백 제외 글자 수 기준의 임시값. 실제로 짧은 정상 문서가 걸리면 낮춘다
MIN_TEXT_CHARS = 20
_converter = None
_converter_lock = threading.Lock()


def _build_converter():
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD, InputFormat.XLSX]
    )
    # 모델은 첫 변환 때 로딩되는 게 기본이라, PDF(레이아웃/표 모델이 있는 무거운 쪽)를
    # 미리 로딩한다.
    # docx/md/xlsx는 가벼운 파이프라인이라 그대로 둔다
    converter.initialize_pipeline(InputFormat.PDF)
    return converter


def get_converter():
    global _converter
    with _converter_lock:
        if _converter is None:
            _converter = _build_converter()
        return _converter


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


def _convert(content: bytes, filename: str) -> tuple[str, list[Position]]:
    # (청크에 쓸 마크다운/원문 텍스트, 근거 위치를 찾을 원문 위치 목록)
    extension = filename[filename.rfind(".") :].lower() if "." in filename else ""
    if extension in _TEXT_EXTENSIONS:
        text = _decode_text(content)
        return text, text_positions(text, markdown=extension == ".md")

    from docling.datamodel.base_models import DocumentStream

    stream = DocumentStream(name=filename, stream=BytesIO(content))
    document = get_converter().convert(stream).document
    return document.export_to_markdown(), docling_positions(document, extension.lstrip("."))


def _extract_text(content: bytes, filename: str) -> str:
    return _convert(content, filename)[0]


async def parse(state: IngestionState) -> dict:
    # 문서 단위로 실패를 처리한다. 읽은 문서는 원문 검색(청크 인덱싱)에 쓰고, 하나라도 못 읽었으면
    # parse_failures에 남겨 카드 경로를 건너뛰게 한다(graph.py 참고). 모두 실패했을 때만 job을
    # 실패시킨다
    parsed_documents: list[ParsedDocument] = []
    positions: dict[str, list[Position]] = {}
    failures: list[str] = []
    for document in state["documents"]:
        filename = document["original_filename"]
        try:
            content = await _download_with_retry(document["storage_path"])
            # docling 변환은 동기 CPU 작업이라 이벤트 루프를 막지 않도록 스레드에서 돌린다
            text, document_positions = await asyncio.to_thread(_convert, content, filename)
        except Exception as e:
            failures.append(f"'{filename}' 파싱 실패: {summarize_error(e)}")
            continue
        if len("".join(text.split())) < MIN_TEXT_CHARS:
            failures.append(f"'{filename}' 파싱 실패: 추출된 텍스트가 거의 없음")
            continue
        parsed_documents.append({"document_id": document["id"], "text": text})
        positions[str(document["id"])] = document_positions
    if not parsed_documents:
        return {"error": " / ".join(failures)}
    update: dict = {
        "parsed_documents": parsed_documents,
        "parse_failures": failures,
        "positions": positions,
    }
    if failures:
        update["warnings"] = [*state.get("warnings", []), *failures]
    return update
