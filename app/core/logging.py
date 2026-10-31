# 로깅 설정을 한 곳에 모은다. 서버 시작 시 main.py에서 setup_logging()을 한 번 호출한다.
#
# - 우리 코드(app.*)는 LOG_LEVEL(기본 INFO), 서드파티(docling, httpx 등)는 WARNING부터만 보인다.
# - 모든 줄에 job_id 앞 8자리가 자동으로 붙는다. 파이프라인이 노드를 실행할 때 job_log_context로
#   job_id를 contextvar에 넣어 두면, 그 안에서 남기는 로그(스레드 포함)에 같이 찍힌다.
# - 문서 내용이 로그로 새지 않게 에러 메시지는 summarize_error로 줄여서 남긴다.
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar

from app.config import settings

# job과 무관한 로그(서버 시작, API 요청 등)는 "-"로 찍힌다
_NO_JOB = "-"
_job_id: ContextVar[str] = ContextVar("job_id", default=_NO_JOB)

_LEVEL_NAMES_KO = {
    "DEBUG": "디버그",
    "INFO": "정보",
    "WARNING": "경고",
    "ERROR": "오류",
    "CRITICAL": "치명",
}
_LOG_FORMAT = "%(asctime)s [%(level_ko)s] [job=%(job_short)s] %(logger_short)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_JOB_ID_DISPLAY_LENGTH = 8

ERROR_MAX_CHARS = 200
# 스택트레이스 한 줄의 최대 길이. pydantic ValidationError는 LLM이 낸 입력값을 줄마다 담는다
_TRACEBACK_LINE_MAX_CHARS = 300

_HANDLER_MARK = "_app_log_handler"


def summarize_error(error: BaseException | str, limit: int = ERROR_MAX_CHARS) -> str:
    # 에러 메시지를 한 줄로 합치고 limit자에서 자른다. pydantic ValidationError나 SDK 에러는
    # 메시지에 LLM 입력/응답(= 문서 내용)을 그대로 담을 수 있어서, 로그와 state["warnings"]에
    # 넣는 에러 문자열은 이 함수를 거친다
    text = " ".join(str(error).split()) or type(error).__name__
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… (총 {len(text)}자 중 {limit}자만 표시)"


def current_job_id() -> str | None:
    job_id = _job_id.get()
    return None if job_id == _NO_JOB else job_id


@contextmanager
def job_log_context(job_id: object):
    # job_id가 없으면(테스트, 스크립트에서 노드 직접 호출) 아무것도 하지 않는다
    if not job_id:
        yield
        return
    token = _job_id.set(str(job_id))
    try:
        yield
    finally:
        _job_id.reset(token)


class _AppFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(_LOG_FORMAT, _DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        record.level_ko = _LEVEL_NAMES_KO.get(record.levelname, record.levelname)
        record.job_short = _job_id.get()[:_JOB_ID_DISPLAY_LENGTH]
        record.logger_short = _short_logger_name(record.name)
        return super().format(record)

    def formatException(self, ei) -> str:
        lines = super().formatException(ei).splitlines()
        return "\n".join(_cut(line, _TRACEBACK_LINE_MAX_CHARS) for line in lines)


def _short_logger_name(name: str) -> str:
    # app.domains.documents.pipeline.nodes.verify -> nodes.verify (서드파티는 그대로)
    if not name.startswith("app."):
        return name
    return ".".join(name.split(".")[-2:])


def _cut(line: str, limit: int) -> str:
    return line if len(line) <= limit else f"{line[:limit]}… ({len(line)}자 중 {limit}자만 표시)"


def _parse_level(value: str) -> int:
    level = logging.getLevelName(value.strip().upper())
    return level if isinstance(level, int) else logging.INFO


def setup_logging() -> None:
    # uvicorn --reload나 테스트에서 여러 번 불려도 핸들러가 겹치지 않게 우리 것만 지우고 다시 단다.
    # root는 WARNING으로 두고 app 로거만 LOG_LEVEL로 열어서 서드파티 로그를 막는다.
    # uvicorn 자체 로그(서버 시작, 요청)는 uvicorn이 자기 포맷으로 따로 찍는다
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_AppFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)

    root.setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(_parse_level(settings.LOG_LEVEL))
