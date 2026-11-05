# 여러 도메인이 같이 쓰는 에러 처리 도우미를 두는 곳.
import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def internal_error(detail: str) -> HTTPException:
    # except 블록 안에서 `raise internal_error(메시지)`로 쓴다.
    # HTTPException만 던지면 서버 로그에 아무것도 남지 않아서,
    # 원래 에러를 로그에 남긴 뒤 500 예외를 돌려준다
    logger.exception(detail)
    return HTTPException(status_code=500, detail=detail)
