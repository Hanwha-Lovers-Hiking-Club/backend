# 여러 도메인이 같이 쓰는 SSE(Server-Sent Events) 도우미를 두는 곳.
from fastapi.sse import ServerSentEvent

from app.core.schemas import CamelModel


def to_sse(event: str, payload: CamelModel) -> ServerSentEvent:
    # FastAPI SSE는 모델을 alias 없이(snake_case) 직렬화해서, camelCase JSON 문자열로 직접 넘긴다
    return ServerSentEvent(event=event, raw_data=payload.model_dump_json(by_alias=True))
