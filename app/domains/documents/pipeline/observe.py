# 파이프라인 관찰(로그) 코드를 한 곳에 모은다.
# - 집계: job 하나가 쓴 LLM/임베딩 호출 수, 토큰, 노드별 시간. 호출은 노드 안의 여러 스레드에서
#   일어나서 job_id(contextvar)로 집계 대상을 찾고 락으로 보호한다.
# - NodeRun: graph._safe가 노드 실행마다 만들어서 시작/완료/실패 로그와 job 요약을 남긴다.
# 비용(원/달러)은 계산하지 않는다: 모델 단가가 바뀌고, kure는 토큰 정보를 주지 않을 수도 있다.
import logging
import threading
import time
from dataclasses import dataclass, field
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.core.logging import current_job_id, job_log_context, summarize_error

logger = logging.getLogger(__name__)


@dataclass
class JobUsage:
    started_at: float = field(default_factory=time.perf_counter)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_calls: int = 0
    embedding_texts: int = 0
    # 노드 이름 -> 걸린 시간(초), 실행 순서대로
    node_seconds: dict[str, float] = field(default_factory=dict)

    def counters(self) -> tuple[int, int, int, int, int]:
        return (
            self.llm_calls,
            self.input_tokens,
            self.output_tokens,
            self.embedding_calls,
            self.embedding_texts,
        )


# ponytail: job이 끝까지 가지 못하고 중간에 취소되면(타임아웃 등) 항목이 남는다. 항목은 숫자 몇 개라
# 무시할 만하고, 서비스 연결 때 job 종료 지점에서 finish_job_usage를 부르면 정리된다
_usage: dict[str, JobUsage] = {}
_lock = threading.Lock()


def start_job_usage(job_id: str) -> JobUsage:
    with _lock:
        return _usage.setdefault(job_id, JobUsage())


def finish_job_usage(job_id: str) -> JobUsage | None:
    with _lock:
        return _usage.pop(job_id, None)


def _current() -> JobUsage | None:
    job_id = current_job_id()
    if job_id is None:
        return None
    return start_job_usage(job_id)


def record_embedding(texts: int, seconds: float, tokens: int | None) -> None:
    logger.debug(
        "임베딩 호출 완료: 텍스트 %d개, %.1f초%s",
        texts,
        seconds,
        f", 토큰 {tokens:,}" if tokens else "",
    )
    usage = _current()
    if usage is None:
        return
    with _lock:
        usage.embedding_calls += 1
        usage.embedding_texts += texts


def _token_counts(response: LLMResult) -> tuple[int, int]:
    # 채팅 모델은 메시지의 usage_metadata에, 일부 경로는 llm_output["token_usage"]에 담긴다
    try:
        metadata = response.generations[0][0].message.usage_metadata  # type: ignore[union-attr]
    except (IndexError, AttributeError):
        metadata = None
    if metadata:
        return metadata.get("input_tokens", 0), metadata.get("output_tokens", 0)
    token_usage = (response.llm_output or {}).get("token_usage") or {}
    return token_usage.get("prompt_tokens", 0), token_usage.get("completion_tokens", 0)


class LlmUsageCallback(BaseCallbackHandler):
    # get_llm()의 모델에 달아 두면 with_structured_output을 거친 호출도 모두 잡힌다.
    # 재시도 호출도 각각 센다(SDK 내부의 전송 재시도는 보이지 않는다)
    def __init__(self):
        self._started: dict[UUID, float] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._started[run_id] = time.perf_counter()

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._started[run_id] = time.perf_counter()

    def _elapsed(self, run_id: UUID) -> float:
        return time.perf_counter() - self._started.pop(run_id, time.perf_counter())

    def on_llm_end(self, response, *, run_id, **kwargs):
        seconds = self._elapsed(run_id)
        input_tokens, output_tokens = _token_counts(response)
        logger.debug(
            "LLM 호출 완료: 입력 토큰 %s, 출력 토큰 %s, %.1f초",
            f"{input_tokens:,}",
            f"{output_tokens:,}",
            seconds,
        )
        usage = _current()
        if usage is None:
            return
        with _lock:
            usage.llm_calls += 1
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens

    def on_llm_error(self, error, *, run_id, **kwargs):
        seconds = self._elapsed(run_id)
        logger.warning("LLM 호출 실패 (%.1f초): %s", seconds, summarize_error(error))
        usage = _current()
        if usage is not None:
            with _lock:
                usage.llm_calls += 1


def describe_delta(before: tuple, usage: JobUsage) -> str:
    # 노드 하나가 쓴 호출/토큰. 쓴 게 없으면 빈 문자열(parse, chunk, save 등)
    with _lock:
        now = usage.counters()
    llm, tokens_in, tokens_out, embeds, texts = (b - a for a, b in zip(before, now, strict=True))
    parts = []
    if llm:
        parts.append(f"LLM {llm}회 (입력 토큰 {tokens_in:,} / 출력 토큰 {tokens_out:,})")
    if embeds:
        parts.append(f"임베딩 {embeds}회 (텍스트 {texts}개)")
    return ", ".join(parts)


def describe_total(usage: JobUsage) -> str:
    with _lock:
        total = time.perf_counter() - usage.started_at
        llm, tokens_in, tokens_out, embeds, texts = usage.counters()
        nodes = " / ".join(f"{name} {sec:.1f}" for name, sec in usage.node_seconds.items())
    return (
        f"총 {total:.1f}초 | 노드별(초) {nodes} | "
        f"LLM {llm}회 (입력 토큰 {tokens_in:,} / 출력 토큰 {tokens_out:,}) | "
        f"임베딩 {embeds}회 (텍스트 {texts}개)"
    )


class NodeRun:
    # 노드 한 번 실행의 관찰 기록: 로그에 job_id를 붙이고, 시작/완료와 걸린 시간,
    # 쓴 LLM/임베딩 호출·토큰을 남긴다. 마지막 노드이거나 error로 끝나면 job 전체 요약도 남긴다.
    # job_id를 노드 실행 스레드 안에서 직접 넣으므로, LangGraph가 동기 노드를 어느 스레드에서
    # 돌리든 로그에 따라온다. 반드시 close()로 끝낸다(try/finally).
    def __init__(self, name: str, state: dict, is_last: bool):
        self.name = name
        self.is_last = is_last
        self.job_id = state.get("job_id")
        self.context = job_log_context(self.job_id)
        self.context.__enter__()
        self.usage = start_job_usage(str(self.job_id)) if self.job_id else None
        self.before = self.usage.counters() if self.usage else None
        self.started = time.perf_counter()
        logger.info("%s 시작", name)

    def log_done(self, result: dict, raised: bool = False) -> None:
        seconds = time.perf_counter() - self.started
        error = result.get("error")
        if error and not raised:
            # 예외로 터진 경우는 graph._safe가 스택트레이스와 함께 이미 남겼다
            logger.error("%s 실패 (%.1f초): %s", self.name, seconds, summarize_error(error))
        elif not error:
            used = describe_delta(self.before, self.usage) if self.usage else ""
            logger.info("%s 완료 (%.1f초)%s", self.name, seconds, f", {used}" if used else "")
        if self.usage is None:
            return
        self.usage.node_seconds[self.name] = seconds
        if error or self.is_last:
            finish_job_usage(str(self.job_id))
            status = "오류로 중단" if error else "완료"
            logger.info("작업 %s: %s", status, describe_total(self.usage))

    def close(self) -> None:
        self.context.__exit__(None, None, None)
