import io
import logging

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.core import logging as app_logging
from app.core.logging import job_log_context, setup_logging, summarize_error
from app.domains.documents.pipeline import observe as U

JOB = "3f2a9c1e-aaaa-bbbb-cccc-000000000001"


@pytest.fixture
def captured():
    # 우리 포맷터로 찍힌 줄을 문자열로 받는다 (실제 콘솔 출력과 같은 모양)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(app_logging._AppFormatter())
    app_logger = logging.getLogger("app")
    old_level = app_logger.level
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.DEBUG)
    yield stream
    app_logger.removeHandler(handler)
    app_logger.setLevel(old_level)


def test_summarize_error_keeps_short_message():
    assert summarize_error(ValueError("boom")) == "boom"


def test_summarize_error_cuts_long_message_and_joins_lines():
    secret = "문서 내용\n" * 200
    text = summarize_error(ValueError(secret))

    assert "\n" not in text
    assert text.startswith("문서 내용 문서 내용")
    assert len(text) < 260
    assert "자만 표시" in text


def test_summarize_error_uses_type_name_for_empty_message():
    assert summarize_error(TimeoutError()) == "TimeoutError"


def test_format_has_korean_level_job_prefix_and_short_logger(captured):
    with job_log_context(JOB):
        logging.getLogger("app.domains.documents.pipeline.nodes.verify").info("판정 완료")
        logging.getLogger("app.domains.documents.pipeline.nodes.verify").warning("건너뜀")

    info_line, warning_line = captured.getvalue().splitlines()
    assert "[정보] [job=3f2a9c1e] nodes.verify: 판정 완료" in info_line
    assert "[경고]" in warning_line


def test_job_id_is_dash_outside_job_context(captured):
    logging.getLogger("app.x").info("서버 시작")
    assert "[job=-]" in captured.getvalue()


def test_traceback_lines_are_cut(captured):
    try:
        raise ValueError("민감한 문서 내용 " * 100)
    except ValueError:
        logging.getLogger("app.x").exception("실패")

    assert "자만 표시" in captured.getvalue()
    assert max(len(line) for line in captured.getvalue().splitlines()) < 400


def test_setup_logging_levels_and_idempotent(monkeypatch):
    root = logging.getLogger()
    old_handlers, old_root, old_app = (
        list(root.handlers),
        root.level,
        logging.getLogger("app").level,
    )
    monkeypatch.setattr(app_logging.settings, "LOG_LEVEL", "debug")
    try:
        setup_logging()
        setup_logging()

        ours = [h for h in root.handlers if getattr(h, app_logging._HANDLER_MARK, False)]
        assert len(ours) == 1
        assert logging.getLogger("app").level == logging.DEBUG
        assert root.level == logging.WARNING  # docling, httpx 등 서드파티는 WARNING부터
        monkeypatch.setattr(app_logging.settings, "LOG_LEVEL", "엉뚱한값")
        setup_logging()
        assert logging.getLogger("app").level == logging.INFO
    finally:
        root.handlers[:] = old_handlers
        root.setLevel(old_root)
        logging.getLogger("app").setLevel(old_app)


class _FakeChat(BaseChatModel):
    fail: bool = False

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.fail:
            raise RuntimeError("llm down")
        message = AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_llm_callback_counts_calls_and_tokens_per_job(captured):
    llm = _FakeChat(callbacks=[U.LlmUsageCallback()])

    with job_log_context(JOB):
        llm.invoke("a")
        llm.invoke("b")
        U.record_embedding(4, 0.5, None)
    usage = U.finish_job_usage(JOB)

    assert (usage.llm_calls, usage.input_tokens, usage.output_tokens) == (2, 200, 40)
    assert (usage.embedding_calls, usage.embedding_texts) == (1, 4)
    assert "LLM 호출 완료: 입력 토큰 100, 출력 토큰 20" in captured.getvalue()


def test_llm_callback_without_job_does_not_track():
    _FakeChat(callbacks=[U.LlmUsageCallback()]).invoke("a")
    assert U.finish_job_usage(JOB) is None


def test_llm_callback_logs_failure_with_short_message(captured):
    llm = _FakeChat(fail=True, callbacks=[U.LlmUsageCallback()])

    with job_log_context(JOB):
        with pytest.raises(RuntimeError):
            llm.invoke("a")
    U.finish_job_usage(JOB)

    assert "[경고]" in captured.getvalue()
    assert "LLM 호출 실패" in captured.getvalue()
    assert "llm down" in captured.getvalue()
