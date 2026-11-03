# 기본 RAG 문서 도메인의 URL 경로만 정의한다 (기존 문서 인덱싱).
# 로직은 쓰지 않고 service.py 함수를 호출만 한다.
from fastapi import APIRouter

from app.domains.baseline_documents import service
from app.domains.baseline_documents.schemas import IndexRequest, IndexResponse

router = APIRouter(prefix="/baseline/documents", tags=["baseline"])


@router.post(
    "/index",
    response_model=IndexResponse,
    summary="[기본 RAG] 기존 문서 인덱싱",
    description=(
        "지식카드 효과 비교용. 이미 업로드된 문서를 청크로 나누고 문맥 설명과 임베딩을 붙여 "
        "baseline_chunks에 저장한다. 같은 문서를 다시 요청하면 기존 청크를 덮어쓴다. "
        "동기 처리라 문서가 많으면 오래 걸린다. 없는 문서 ID가 섞여 있으면 404."
    ),
)
async def index_documents(req: IndexRequest):
    return await service.index_documents(req.document_ids)
