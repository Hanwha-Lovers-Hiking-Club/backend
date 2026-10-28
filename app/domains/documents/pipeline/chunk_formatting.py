# 청크 목록을 LLM 프롬프트에 넣을 XML 태그 문자열로 포맷한다. extract/draft_card가 공용으로 쓴다.
# 태그를 쓰는 이유: 청크 본문(마크다운 원문)에 이미 #, ---, ``` 가 있어서 구분자 방식은 겹칠 위험이
# 있다.
from app.domains.documents.pipeline.state import Chunk


def format_chunks(chunks: list[Chunk]) -> str:
    return "\n".join(
        f'<chunk id="{c["id"]}" location="{c["location"]}">\n{c["text"]}\n</chunk>' for c in chunks
    )
