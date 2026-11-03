# 기본 RAG 챗봇 검색 그래프의 노드들이 주고받는 상태를 정의한다.
# 그래프 순서: rewrite -> retrieve -> rerank -> expand
from typing import TypedDict

from app.domains.chat.schemas import ChatMessage


# baseline_chunks의 한 행 + 검색 점수
class RetrievedChunk(TypedDict):
    id: str
    document_id: str
    chunk_index: int
    location: str | None
    # 청크 본문. 답변 컨텍스트와 근거 표시에 쓴다
    content: str
    # 헤더 + 문맥 설명 + 본문. BM25와 재순위의 대상이다
    search_text: str
    # retrieve 단계에서는 RRF 점수, rerank 단계 이후에는 재순위 점수(키가 없으면 RRF 점수 그대로)
    score: float


# expand 노드의 출력. generate가 답변 컨텍스트로 쓴다
class ContextChunk(RetrievedChunk):
    # 앞 청크 + 이 청크 + 뒤 청크 본문. 근거 표시에는 쓰지 않고 답변 컨텍스트에만 쓴다
    context: str


class RetrievalState(TypedDict, total=False):
    # 입력: 사용자 질문과 이전 대화 내역
    message: str
    history: list[ChatMessage]
    # rewrite 출력. 첫 번째가 대화 내역을 반영한 독립 질문이고, 나머지는 다른 표현 질문이다
    queries: list[str]
    # retrieve 출력. RRF 순위 상위 후보
    candidates: list[RetrievedChunk]
    # rerank 출력. 답변에 쓸 상위 청크
    reranked: list[RetrievedChunk]
    # expand 출력. reranked와 같은 순서
    contexts: list[ContextChunk]
