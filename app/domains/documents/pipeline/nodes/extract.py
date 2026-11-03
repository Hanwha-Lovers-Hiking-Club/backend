# 노드: job에 속한 모든 문서를 LLM 호출 한 번에 보여 주고, "경험" 단위로 근거를 뽑는다.
#
# 방식은 비교 실험(scripts/compare_extraction.py)으로 정했다. 문서별로 따로 뽑아 나중에 잇는
# 방식은 문서 하나만 보면 무엇이 중요한지 몰라서 잘못 잇거나 쪼갰고, 한꺼번에 넣는 방식은 같은
# 맥락을 직접 봐서 ID를 가린 문서에서도 정확히 묶었다. 이 방식의 약점이던 빠뜨림은 모델을
# gpt-5.6-terra로 바꿔서 해결됐다.
#
# LLM 출력은 그대로 믿지 않고 코드로 두 번 거른다.
# (1) 인용 검사: 근거의 인용이 그 문서 원문에 실제로 있는지 본다. 없으면 그 근거만 버린다.
# (2) 근거 종류 필터: 원인·결정·조치·결과 근거가 하나도 없는 경험(계획·관찰만 있는 후보)은 버린다.
#     실험에서 모델이 낸 잘못된 경험(승인 안 된 후보 과제 등)은 전부 이 규칙으로 걸러졌다.
import logging
import re
from pathlib import Path

import tiktoken
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import summarize_error
from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry
from app.domains.documents.pipeline.models import Evidence, Experience, Experiences
from app.domains.documents.pipeline.state import Chunk, IngestionState

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "extract.md").read_text(encoding="utf-8")
_tokenizer = tiktoken.get_encoding("cl100k_base")

# ponytail: 보수적으로 잡은 임시값(gpt-5.6-terra 컨텍스트 ~1.05M 토큰 대비 넉넉하다).
# 실험의 가장 큰 프로젝트(문서 5개)가 약 2.5만 토큰이었다
MAX_INPUT_TOKENS = 200_000

# 이 중 하나도 없으면 실제로 일어난 일(원인 확인, 결정, 조치, 결과)이 없는 경험이다
_FACT_KINDS = {"cause", "decision", "action", "result"}

# 인용 비교는 글자와 숫자만 본다. PDF 변환문은 줄바꿈 자리에 공백이 끼고("대 사"),
# 모델은 기호를 빼고 인용하는 일이 흔하다
_NON_TEXT = re.compile(r"[\W_]+")
# 모델은 인용 중간을 "..."로 줄이거나 떨어진 문장·표 칸을 이어 붙인다. 조각마다 원문에 있는지 본다
_QUOTE_SPLIT = re.compile(r"\.\.\.|…|\[\.\.\.\]|(?<=[.다음함됨임])\s+|\n|\s\|\s")
# 이보다 짧은 조각(표의 "Pass", 숫자 하나 등)은 어디에나 있을 수 있어서 따로 보지 않는다
_MIN_PIECE_CHARS = 6


def _plain(text: str) -> str:
    return _NON_TEXT.sub("", text)


def _aliases(state: IngestionState) -> tuple[dict[str, Chunk], dict[str, str]]:
    # 실제 청크 id(<uuid>:<번호>)는 길어서 프롬프트에는 "D<문서 번호>:<번호>" 별칭으로 넣는다.
    # (별칭 -> 청크, 문서 별칭 -> 파일명)을 돌려준다. 문서 번호는 업로드 순서다
    doc_alias = {d["id"]: f"D{i}" for i, d in enumerate(state["documents"], 1)}
    names = {doc_alias[d["id"]]: d["original_filename"] for d in state["documents"]}
    chunks = {f"{doc_alias[c['document_id']]}:{c['index']}": c for c in state["chunks"]}
    return chunks, names


def _format(chunks: dict[str, Chunk], names: dict[str, str]) -> str:
    out, current = [], None
    for alias, c in chunks.items():
        doc = alias.split(":")[0]
        if doc != current:
            current = doc
            out.append(f"\n# 문서 {doc}: {names[doc]}")
        out.append(f'<chunk id="{alias}" location="{c["location"]}">\n{c["text"]}\n</chunk>')
    return "\n".join(out)


def _ground(evidence: Evidence, chunks: dict[str, Chunk]) -> Evidence | None:
    # 인용이 원문에 있으면 chunk_id를 실제 청크 id로 바꿔 돌려주고, 없으면 None.
    # 모델이 청크 id를 잘못 적은 경우(같은 문서의 다른 청크에 인용이 있음)는 id를 고친다
    doc = evidence.chunk_id.split(":")[0]
    doc_chunks = [(alias, c) for alias, c in chunks.items() if alias.split(":")[0] == doc]
    quote = _plain(evidence.quote)
    pieces = [
        p for p in map(_plain, _QUOTE_SPLIT.split(evidence.quote)) if len(p) >= _MIN_PIECE_CHARS
    ]
    pieces = pieces or ([quote] if quote else [])
    doc_text = "".join(_plain(c["text"]) for _, c in doc_chunks)
    if not pieces or not all(p in doc_text for p in pieces):
        return None
    chunk = chunks.get(evidence.chunk_id)
    if chunk is None or pieces[0] not in _plain(chunk["text"]):
        # 인용 조각이 청크 경계에 걸쳐 있으면 어느 청크에도 통째로 없다. 그때는 원래 청크를 둔다
        found = next((c for _, c in doc_chunks if pieces[0] in _plain(c["text"])), None)
        chunk = found or chunk or doc_chunks[0][1]
    return evidence.model_copy(update={"chunk_id": chunk["id"]})


def _keep(experience: Experience, chunks: dict[str, Chunk]) -> Experience | None:
    evidences = [e for e in (_ground(e, chunks) for e in experience.evidences) if e]
    dropped = len(experience.evidences) - len(evidences)
    if dropped:
        logger.warning("extract: 원문에 없는 인용 %d개 제거 (%s)", dropped, experience.title)
    if not {e.kind for e in evidences} & _FACT_KINDS:
        logger.info("extract: 원인·결정·조치·결과 근거가 없어 경험 제외 (%s)", experience.title)
        return None
    return experience.model_copy(update={"evidences": evidences})


def extract(state: IngestionState) -> dict:
    # 청크가 없으면 LLM에 빈 입력을 주지 않는다(빈 입력에서 내용을 지어낼 수 있음)
    if not state["chunks"]:
        return {"experiences": []}

    chunks, names = _aliases(state)
    message = _format(chunks, names)
    if len(_tokenizer.encode(message, disallowed_special=())) > MAX_INPUT_TOKENS:
        return {"error": f"추출 입력이 너무 큽니다 (토큰 {MAX_INPUT_TOKENS}개 초과)"}

    llm = get_llm().with_structured_output(Experiences)
    try:
        # 지시문은 시스템 메시지, 문서 본문은 사용자 메시지로 분리해서
        # 본문 속 문장이 지시문과 섞이지 않게 한다
        result = invoke_with_retry(llm, [SystemMessage(_PROMPT), HumanMessage(message)])
    except Exception as e:
        return {"error": f"경험 추출 실패: {summarize_error(e)}"}

    experiences = [e for e in (_keep(e, chunks) for e in result.experiences) if e]
    logger.info(
        "extract: 문서 %d개, 청크 %d개 -> 경험 %d개 (LLM 응답 %d개)",
        len(names),
        len(chunks),
        len(experiences),
        len(result.experiences),
    )
    return {"experiences": experiences}
