# DB에 넣기 전에 문자열을 정리하는 도우미.
from typing import Any


def strip_nul(value: Any) -> Any:
    # Postgres text/jsonb는 NUL 문자(\u0000)를 못 넣어서(22P05) 카드/청크 insert가 통째로 실패한다.
    # LLM 출력이나 PDF 변환 결과에 섞일 수 있어서(실제 업로드 시험에서 카드 1장이 이 오류로
    # 저장 실패, 출처는 확인 못 함) 저장 직전에 dict/list 안의 문자열에서 지운다.
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    return value
