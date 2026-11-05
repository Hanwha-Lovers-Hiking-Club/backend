# 공급자별 오류의 일시성을 분류하고 호출 하나의 재시도 횟수와 대기 시간을 제한한다.

import asyncio
import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def exception_chain(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__


def transient_failure(exc):
    explicit = getattr(exc, "retryable", None)
    if explicit is not None:
        return explicit is True
    if getattr(exc, "code", None) not in {
        "LLM_API_ERROR",
        "LLM_TIMEOUT",
        "LLM_CONNECTION_ERROR",
        "LLM_RATE_LIMITED",
    }:
        return False
    for cause in exception_chain(exc):
        status = getattr(cause, "status_code", None)
        if status is None:
            status = getattr(getattr(cause, "response", None), "status_code", None)
        if isinstance(status, int):
            return status in (408, 429) or 500 <= status < 600
    return True


def server_retry_after(exc):
    for cause in exception_chain(exc):
        value = getattr(cause, "retry_after", None)
        if value is None:
            response = getattr(cause, "response", None)
            value = getattr(response, "headers", {}).get("retry-after")
        if value is not None:
            try:
                return float(value)
            except (ValueError, TypeError):
                try:
                    date = parsedate_to_datetime(value)
                    return max(0, (date - datetime.now(UTC)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    continue
    return None


def retry_delay(exc, attempt, *, delay_seconds, max_wait_seconds):
    requested = server_retry_after(exc)
    if requested is not None:
        if not math.isfinite(requested) or requested < 0 or requested > max_wait_seconds:
            return None
        return requested
    return min(max_wait_seconds, delay_seconds * 2 ** (attempt - 1))


async def retry_call(call, *, max_attempts, delay_seconds, max_wait_seconds):
    for attempt in range(1, max_attempts + 1):
        try:
            return await call()
        except Exception as exc:
            if not transient_failure(exc) or attempt == max_attempts:
                raise
            delay = retry_delay(
                exc, attempt, delay_seconds=delay_seconds, max_wait_seconds=max_wait_seconds
            )
            if delay is None:
                raise
            if delay:
                await asyncio.sleep(delay)
