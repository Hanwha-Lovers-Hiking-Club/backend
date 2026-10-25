from fastapi import APIRouter, Query

from app.domains.knowledge_cards import service
from app.domains.knowledge_cards.schemas import CardSort, KnowledgeCardListResponse

router = APIRouter(prefix="/knowledge", tags=["knowledge_cards"])


@router.get(
    "/cards",
    response_model=KnowledgeCardListResponse,
    summary="지식카드 목록 조회",
    description="정렬·페이지네이션 조건에 맞는 active 지식카드 목록과 전체 개수를 반환한다.",
)
async def list_cards(
    sort: CardSort = Query(
        "recent", description="recent: 최근 수정순 / created: 최신 생성순 / name: 제목순"
    ),
    page: int = Query(1, ge=1, description="페이지 번호 (1부터)"),
    page_size: int = Query(12, alias="pageSize", ge=1, le=100, description="페이지당 카드 수"),
):
    return await service.list_cards(sort, page, page_size)
