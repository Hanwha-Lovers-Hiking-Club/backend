# 노드: 원본 파일에서 텍스트를 추출한다.
# 공정한 비교를 위해 main의 documents 파이프라인과 같은 다운로드·변환 함수를 그대로 쓴다.
# 주의: _extract_text는 main 쪽 private 함수다. main을 merge할 때 이름이나 모양이
# 바뀌었는지 확인한다.
import asyncio

from app.domains.baseline_documents.pipeline.state import IngestionState, ParsedDocument
from app.domains.documents import repository as documents_repository
from app.domains.documents.pipeline.nodes.parse import _extract_text


async def parse(state: IngestionState) -> dict:
    parsed_documents: list[ParsedDocument] = []
    for document in state["documents"]:
        filename = document["original_filename"]
        try:
            content = await asyncio.to_thread(
                documents_repository.download_file, document["storage_path"]
            )
            # docling 변환은 동기 CPU 작업이라 이벤트 루프를 막지 않도록 스레드에서 돌린다
            text = await asyncio.to_thread(_extract_text, content, filename)
        except Exception as e:
            return {"error": f"'{filename}' 파싱 실패: {e}"}
        parsed_documents.append(
            {"document_id": document["id"], "title": document["title"], "text": text}
        )
    return {"parsed_documents": parsed_documents}
