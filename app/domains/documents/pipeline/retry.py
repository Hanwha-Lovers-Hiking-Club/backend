# 파이프라인 공통 재시도: 지수 백오프 + 지터, 재시도마다 로그를 남긴다.
#
# 멱등한 호출(읽기, LLM, 임베딩)에만 쓴다. DB insert에는 쓰지 않는다 —
# 타임아웃으로 끝난 요청이 실제로는 저장됐을 수 있어서,
# 재시도하면 카드가 중복되거나 청크가 유니크 위반을 낸다.
# OpenAI SDK가 이미 전송 오류(연결 오류, 408/409/429/5xx)를 재시도하므로,
# SDK 클라이언트를 쓰는 호출은 retry_on을 좁혀서(예: 응답 형식 오류만) 재시도가 곱해지지 않게 한다.
import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _delay(backoff: float, attempt: int) -> float:
    return backoff * 2**attempt + random.uniform(0, backoff / 2) if backoff else 0.0


def retry_sync(
    fn: Callable[[], T],
    *,
    retries: int = 2,
    backoff: float = 1.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    name: str = "호출",
) -> T:
    for attempt in range(retries + 1):
        try:
            return fn()
        except retry_on as e:
            if attempt == retries:
                raise
            delay = _delay(backoff, attempt)
            logger.warning(
                "%s 실패, 재시도 %d/%d (%.1f초 뒤): %s", name, attempt + 1, retries, delay, e
            )
            if delay:
                time.sleep(delay)
    raise AssertionError("unreachable")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int = 2,
    backoff: float = 1.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    name: str = "호출",
) -> T:
    for attempt in range(retries + 1):
        try:
            return await fn()
        except retry_on as e:
            if attempt == retries:
                raise
            delay = _delay(backoff, attempt)
            logger.warning(
                "%s 실패, 재시도 %d/%d (%.1f초 뒤): %s", name, attempt + 1, retries, delay, e
            )
            if delay:
                await asyncio.sleep(delay)
    raise AssertionError("unreachable")
