import logging

from app.core.errors import internal_error


def test_internal_error_returns_500_with_detail():
    try:
        raise ValueError("원래 에러")
    except ValueError:
        error = internal_error("조회하지 못했습니다.")

    assert error.status_code == 500
    assert error.detail == "조회하지 못했습니다."


def test_internal_error_logs_original_exception(caplog):
    caplog.set_level(logging.ERROR, logger="app")

    try:
        raise ValueError("원래 에러")
    except ValueError:
        internal_error("조회하지 못했습니다.")

    assert "조회하지 못했습니다." in caplog.text
    assert "ValueError: 원래 에러" in caplog.text
