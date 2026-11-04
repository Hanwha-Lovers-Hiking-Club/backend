# txt와 md는 docling 없이 원문 그대로 읽는다. md를 docling에 넣으면 굵은 글씨 안의 인라인 코드가
# 짝이 안 맞는 코드펜스로 바뀌어 청크 노드가 그 뒤의 헤딩을 잃는다(실제 문서에서 확인된 문제).
import pytest

from app.domains.documents.pipeline.nodes import chunk as chunk_node
from app.domains.documents.pipeline.nodes import parse as P

MD = (
    "# 결정\n\n"
    "**서울 리전에 둔다. 구성은 `scripts/provision.py`로 한다.**\n\n"
    "## 배경\n\n본문 " + "내용 " * 60 + "\n\n"
    "## 결과\n\n본문 " + "결과 " * 60 + "\n"
)


@pytest.fixture(autouse=True)
def no_docling(monkeypatch):
    def forbidden():
        raise AssertionError("txt/md는 docling을 쓰면 안 된다")

    monkeypatch.setattr(P, "get_converter", forbidden)


def test_md_is_returned_as_is_without_docling():
    assert P._extract_text(MD.encode("utf-8"), "a.md") == MD


def test_uppercase_md_extension_is_also_text():
    assert P._extract_text(MD.encode("utf-8"), "README.MD") == MD


def test_md_keeps_all_headings_for_chunking():
    text = P._extract_text(MD.encode("utf-8"), "a.md")

    chunks = chunk_node.chunk({"parsed_documents": [{"document_id": "d", "text": text}]})["chunks"]

    # 짧은 첫 섹션(# 결정)은 청크 노드가 다음 섹션과 합치므로 location은 뒤쪽 것이다.
    # 중요한 건 인라인 코드 뒤의 헤딩(배경, 결과)이 사라지지 않는 것
    assert [c["location"] for c in chunks] == ["결정 · 배경", "결정 · 결과"]
    assert "`scripts/provision.py`" in chunks[0]["text"]


def test_md_with_utf8_bom_and_cp949_is_decoded():
    assert P._extract_text("﻿# 제목\n".encode("utf-8"), "a.md") == "# 제목\n"
    assert P._extract_text("# 제목\n".encode("cp949"), "a.md") == "# 제목\n"
