from uuid import uuid4

from app.domains.documents.pipeline.nodes import chunk as C

LONG = "이 문장은 섹션이 병합 기준을 넘도록 길게 쓴 본문입니다. " * 12


def _run(text: str) -> list[dict]:
    return C.chunk({"parsed_documents": [{"document_id": uuid4(), "text": text}]})["chunks"]


def test_chunks_carry_their_index():
    chunks = _run(f"# A\n{LONG}\n# B\n{LONG}\n")
    assert [c["index"] for c in chunks] == list(range(len(chunks)))
    assert all(c["id"].endswith(f":{c['index']}") for c in chunks)


def test_heading_only_section_merges_into_next():
    chunks = _run(f"# 제목\n\n## 소제목\n{LONG}\n")
    assert len(chunks) == 1
    assert "# 제목" in chunks[0]["text"] and chunks[0]["location"] == "제목 · 소제목"


def test_small_last_section_merges_into_previous():
    chunks = _run(f"# A\n{LONG}\n# B\n끝\n")
    assert len(chunks) == 1 and chunks[0]["location"] == "A" and "끝" in chunks[0]["text"]


def test_big_table_split_by_rows_with_header_repeated():
    header = "| 이슈 | 원인 |\n|---|---|\n"
    rows = [f"| 이슈{i} | 캐시 만료 원인 설명 {i} |\n" for i in range(200)]
    chunks = _run("# 표\n" + header + "".join(rows))
    assert len(chunks) > 1
    assert all(c["text"].lstrip().startswith("# 표") or header in c["text"] for c in chunks)
    assert all(C._count(c["text"]) <= C.CHUNK_SIZE_TOKENS for c in chunks)
    body = "".join(c["text"] for c in chunks)
    assert all(row in body for row in rows)


def test_code_block_that_fits_is_not_cut():
    code = "```python\n" + "x = 1\n" * 40 + "```\n"
    chunks = _run(f"# 코드\n{LONG * 3}\n{code}{LONG * 3}\n")
    assert sum(code in c["text"] for c in chunks) == 1
