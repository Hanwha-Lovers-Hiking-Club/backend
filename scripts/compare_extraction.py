# ruff: noqa: E501 — 프롬프트 문자열은 실험한 텍스트 그대로 둬야 해서 줄을 나누지 않는다
"""추출 방식 비교 실험: (A) 프로젝트 문서 전부를 한 번에 넣기 vs (B) 문서별 추출 + 후보 연결.

DB/Storage에 쓰지 않고 OpenAI만 호출한다. 로컬 프로젝트 폴더 하나 = 업로드 묶음 하나.
두 방식은 같은 청크, 같은 근거 스키마, 같은 인용 검사를 쓴다. 차이는 "LLM이 한 번에 무엇을 보는가"뿐이다.
--mask를 주면 이슈 ID, 파일명, 문서 번호, 연-월-일 날짜를 가린 입력으로도 같은 실험을 돌린다
(ID나 시점이 명확하지 않은 문서에서도 연결이 되는지 보려는 것).
evaluation/ 폴더는 읽지 않는다. 채점은 사람이 결과 파일을 보고 따로 한다.

사용법 (레포 루트에서):
    uv run python scripts/compare_extraction.py --project "<프로젝트 폴더>" --mask
결과: --out 폴더(기본 runs/compare/<폴더명>-<시각>)의 report.md, results.json
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.domains.documents.pipeline.llm import MODEL_NAME, invoke_with_retry  # noqa: E402
from app.domains.documents.pipeline.nodes.chunk import chunk  # noqa: E402
from app.domains.documents.pipeline.nodes.parse import _extract_text  # noqa: E402

Kind = Literal[
    "observation",
    "hypothesis",
    "rejected_hypothesis",
    "cause",
    "decision",
    "action",
    "result",
    "plan",
]


class Evidence(BaseModel):
    kind: Kind
    statement: str
    chunk_id: str
    quote: str


class Experience(BaseModel):
    title: str
    summary: str
    link_hints: list[str]
    evidences: list[Evidence]


class Experiences(BaseModel):
    experiences: list[Experience]


class DocEvent(BaseModel):
    summary: str
    is_experience: bool
    reason: str


class DocInventory(BaseModel):
    document_id: str
    events: list[DocEvent]


# A2: 문서별 사건 목록을 먼저 쓰게 해서(필드 순서대로 생성된다) 문서 하나짜리 경험을 빠뜨리지 않게 한다
class InventoriedExperiences(BaseModel):
    inventory: list[DocInventory]
    experiences: list[Experience]


class Group(BaseModel):
    reason: str
    candidate_ids: list[str]
    title: str


class Grouping(BaseModel):
    groups: list[Group]


EVIDENCE_RULES = """\
## 근거 종류(kind)
- observation: 실제로 관찰·측정한 현상, 증상, 수치. 조치 전에 관찰한 실패도 여기다.
- hypothesis: 의심하거나 추정한 원인. 아직 확인되지 않은 것.
- rejected_hypothesis: 검토한 뒤 기각한 가설. statement에 기각한 근거를 같이 쓴다.
- cause: 근거와 함께 확정된 원인.
- decision: 채택하거나 기각한 선택과 그 이유(대안 비교 포함).
- action: 실제로 수행·구현·배포·적용한 조치.
- result: 그 조치를 수행한 뒤 관찰·측정한 결과. 남은 실패, 표본 수, 환경, 단위를 그대로 보존한다.
- plan: 앞으로 할 일, 예정, 목표, 기대 효과, 승인되지 않은 제안.

## 규칙
- 경험은 하나의 기술적 문제나 의사결정 사건이다. 단순 일정, 인원 배치, 회의 운영은 경험이 아니다.
- quote는 원문을 고치지 않고 그대로 옮긴 짧은 구절(한두 문장 또는 표의 한 행)이다.
  chunk_id는 그 구절이 실제로 있는 청크의 id를 정확히 쓴다.
- statement는 quote가 말하는 내용만 쓴다. 원문에 없는 원인, 성과, 수치를 덧붙이지 않는다.
- 결정이나 계획만 있고 결과가 없는 경험도 나열한다. 결과가 없으면 result를 만들지 않는다.
- link_hints에는 이 경험을 다른 기록과 이을 수 있는 단서를 원문 표현대로 쓴다: 다루는 기능·데이터·시스템,
  증상, 조치 내용, 시점, 다른 문서를 가리키는 표현. 이슈 번호가 없어도 내용 단서를 쓴다.
"""

PROMPT_ALL = (
    "당신은 한 프로젝트의 산출물 여러 개를 읽고 재사용할 수 있는 기술 경험을 찾는다. "
    "입력은 같은 프로젝트의 문서들이고, 각 청크에 id와 문서 위치가 붙어 있다.\n\n"
    "같은 사건의 증상·원인·결정·결과가 여러 문서에 나뉘어 있으면 하나의 경험으로 묶는다. 단, 같은 "
    "사건이라는 근거가 원문에 있을 때만 묶는다. 같은 기술이나 비슷한 증상이라도 기능 범위, 발생 시점, "
    "원인, 조치가 다르면 별개의 경험이다. 애매하면 나눈다.\n\n" + EVIDENCE_RULES
)

PROMPT_ALL2 = (
    "당신은 한 프로젝트의 산출물 여러 개를 읽고 재사용할 수 있는 기술 경험을 찾는다. "
    "입력은 같은 프로젝트의 문서들이고, 각 청크에 id와 문서 위치가 붙어 있다.\n\n"
    "## 우선순위\n"
    "잘못된 경험을 만드는 것이 경험을 빠뜨리는 것보다 나쁘다. 원문 근거가 없는 내용은 쓰지 않고, "
    "같은 사건이라는 근거가 없으면 묶지 않는다. 그 범위 안에서 경험을 빠뜨리지 않는다.\n\n"
    "## 작업 순서\n"
    "1. inventory: 입력의 모든 문서를 하나도 건너뛰지 않고, 문서마다 그 문서가 다루는 사건(문제, 조사, "
    "결정, 변경)을 모두 적는다. 사건마다 기술 경험인지(is_experience)와 그 이유를 쓴다.\n"
    "   - 기술 경험이다: 실제로 겪은 기술 문제와 그 조사·조치, 실제로 내린(승인·채택한) 기술 결정. "
    "결과가 아직 없어도 실제로 내린 결정이면 경험이다.\n"
    "   - 기술 경험이 아니다: 승인되지 않은 제안, 검토할 후보 목록, 일정·인원·회의 운영만 있는 것.\n"
    "2. experiences: inventory에서 경험으로 표시한 사건을 하나도 빠뜨리지 않고 경험으로 만든다. "
    "문서 하나에만 있는 사건도 별개의 경험이다. 한 문서에 서로 다른 사건이 여러 개 있으면 나눈다.\n\n"
    "## 묶기\n"
    "같은 사건의 증상·원인·결정·결과가 여러 문서에 나뉘어 있으면 하나의 경험으로 묶는다. 단, 같은 "
    "사건이라는 근거가 원문에 있을 때만 묶는다. 같은 기술이나 비슷한 증상이라도 기능 범위, 발생 시점, "
    "원인, 조치가 다르면 별개의 경험이다. 같은 문서에 함께 있다는 것만으로는 같은 사건이 아니다. "
    "애매하면 나눈다.\n\n"
    + EVIDENCE_RULES.replace(
        "- 결정이나 계획만 있고 결과가 없는 경험도 나열한다. 결과가 없으면 result를 만들지 않는다.",
        "- 결과가 없으면 result를 만들지 않는다. 계획·예정·기대 효과는 plan이며 result가 아니다.",
    )
)

PROMPT_ONE = (
    "당신은 한 프로젝트 산출물 중 문서 하나를 읽고 기술 경험 후보를 찾는다. 이 문서에 있는 내용만 쓴다. "
    "같은 경험의 다른 부분(원인, 결정, 결과)은 다른 문서에 있을 수 있으니, 이 문서에 일부만 있어도 "
    "후보로 남긴다. 한 문서에 서로 다른 사건이 여러 개 있으면 후보를 나눈다. 경험이 없으면 빈 목록이다.\n\n"
    + EVIDENCE_RULES
)

PROMPT_LINK = """\
당신은 같은 프로젝트의 여러 문서에서 따로 뽑은 경험 후보들을 보고, 같은 사건을 다룬 후보끼리 묶는다.

- 같은 사건이라는 근거가 있을 때만 묶는다: 같은 대상(기능·데이터·시스템)에서 같은 증상이나 변경을 다루고,
  시점과 인과(증상 -> 원인 -> 결정 -> 조치 -> 결과)가 이어지거나, 한 기록이 다른 기록을 명시적으로 가리킨다.
- 같은 기술이나 비슷한 증상을 공유해도 기능 범위, 발생 시점, 원인, 조치가 다르면 묶지 않는다.
- 애매하면 묶지 않는다. 같은 문서의 후보끼리도 같은 사건이면 묶을 수 있다.
- 모든 후보 id는 정확히 한 그룹에 들어가야 한다. 혼자인 후보는 단독 그룹이다.
- reason에 묶은(또는 혼자 둔) 근거를 원문 단서로 쓴다.
"""

_NON_TEXT = re.compile(r"[\W_]+")
_ISSUE_ID = re.compile(r"\b[A-Z][A-Z0-9]{0,9}(?:-[A-Z0-9]+)*-\d+(?:\.\d+)*\b")
_FULL_DATE = re.compile(
    r"\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?"
)


def _plain(text: str) -> str:
    return _NON_TEXT.sub("", text)


def _mask(text: str, stems: list[str]) -> str:
    # 업로드한 파일명에서 나온 토큰(예: B01, ADR-012)과 이슈 ID 형태, 연-월-일 날짜를 가린다.
    # 시각(HH:MM)과 월/일(9/18)은 남긴다: 숫자 표·비율과 구분이 안 돼 함께 망가진다
    for stem in sorted(stems, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(stem)}\b", "[문서]", text)
    text = _ISSUE_ID.sub("[ID]", text)
    return _FULL_DATE.sub("[날짜]", text)


def _stem_tokens(names: list[str]) -> list[str]:
    tokens = set()
    for name in names:
        stem = Path(name).stem
        tokens.add(stem)
        tokens.update(
            t
            for t in re.split(r"[_\s]+", stem)
            if re.search(r"[A-Za-z]", t) and re.search(r"\d", t)
        )
    return [t for t in tokens if len(t) >= 2]


def _load(folder: Path, masked: bool) -> tuple[list[dict], dict[str, str]]:
    files = sorted(f for f in folder.iterdir() if not f.name.startswith("."))
    stems = _stem_tokens([f.name for f in files])
    parsed, labels = [], {}
    for i, f in enumerate(files, 1):
        text = _extract_text(f.read_bytes(), f.name)
        doc_id = f"D{i}"
        labels[doc_id] = f"문서 {i} ({f.suffix[1:]})" if masked else f.name
        parsed.append({"document_id": doc_id, "text": _mask(text, stems) if masked else text})
    return chunk({"parsed_documents": parsed})["chunks"], labels


def _format(chunks: list[dict], labels: dict[str, str]) -> str:
    out, current = [], None
    for c in chunks:
        if c["document_id"] != current:
            current = c["document_id"]
            out.append(f"\n# 문서 {current}: {labels[current]}")
        out.append(f'<chunk id="{c["id"]}" location="{c["location"]}">\n{c["text"]}\n</chunk>')
    return "\n".join(out)


def _check(evidence: Evidence, chunks_by_id: dict, doc_chunks: dict) -> str:
    quote = _plain(evidence.quote)
    chunk = chunks_by_id.get(evidence.chunk_id)
    if not quote:
        return "empty"
    if chunk and quote in _plain(chunk["text"]):
        return "ok"
    doc = evidence.chunk_id.split(":")[0]
    if any(quote in _plain(c["text"]) for c in doc_chunks.get(doc, [])):
        return "wrong_chunk"
    return "not_found"


# 실험용 모델 설정. 파이프라인의 llm.get_llm()은 건드리지 않는다
_MODEL = {"name": MODEL_NAME, "reasoning": None}
_usage = {"calls": 0, "input": 0, "output": 0, "reasoning": 0}


class _UsageCallback(BaseCallbackHandler):
    # output에는 추론 토큰이 포함된다(OpenAI 청구 기준). reasoning은 그중 추론 토큰만 따로 센 값
    def on_llm_end(self, response, **kwargs):
        for generations in response.generations:
            for g in generations:
                meta = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                _usage["calls"] += 1
                _usage["input"] += meta.get("input_tokens", 0)
                _usage["output"] += meta.get("output_tokens", 0)
                details = meta.get("output_token_details") or {}
                _usage["reasoning"] += details.get("reasoning", 0) or 0


def _llm() -> ChatOpenAI:
    kwargs = {
        "model": _MODEL["name"],
        "api_key": settings.OPENAI_API_KEY,
        "timeout": 600,
        "callbacks": [_UsageCallback()],
    }
    if _MODEL["reasoning"]:
        kwargs["reasoning_effort"] = _MODEL["reasoning"]  # 추론 모델은 temperature 고정을 못 한다
    else:
        kwargs["temperature"] = 0
    return ChatOpenAI(**kwargs)


def _call(schema, prompt: str, message: str):
    return invoke_with_retry(
        _llm().with_structured_output(schema), [SystemMessage(prompt), HumanMessage(message)]
    )


def run_all(chunks, labels) -> list[dict]:
    result = _call(Experiences, PROMPT_ALL, _format(chunks, labels))
    return [{"experience": e.model_dump(), "link_reason": None} for e in result.experiences]


def run_all2(chunks, labels) -> tuple[list[dict], list[dict]]:
    result = _call(InventoriedExperiences, PROMPT_ALL2, _format(chunks, labels))
    results = [{"experience": e.model_dump(), "link_reason": None} for e in result.experiences]
    return results, [i.model_dump() for i in result.inventory]


def run_per_doc(chunks, labels) -> list[dict]:
    candidates: list[tuple[str, Experience]] = []
    for doc_id in labels:
        doc_chunks = [c for c in chunks if c["document_id"] == doc_id]
        result = _call(Experiences, PROMPT_ONE, _format(doc_chunks, labels))
        candidates += [(doc_id, e) for e in result.experiences]
    if not candidates:
        return []

    ids = {f"C{i}": pair for i, pair in enumerate(candidates, 1)}
    lines = []
    for cid, (doc_id, e) in ids.items():
        ev = "\n".join(f"  - [{x.kind}] {x.statement} | 원문: {x.quote}" for x in e.evidences)
        lines.append(
            f"## {cid} (문서 {doc_id}: {labels[doc_id]})\n제목: {e.title}\n요약: {e.summary}\n"
            f"단서: {', '.join(e.link_hints)}\n근거:\n{ev}"
        )
    grouping = _call(Grouping, PROMPT_LINK, "\n\n".join(lines))

    used, merged = set(), []
    for g in grouping.groups:
        members = [c for c in g.candidate_ids if c in ids and c not in used]
        if not members:
            continue
        used.update(members)
        merged.append((g.title, g.reason, members))
    merged += [
        (ids[c][1].title, "(연결 단계가 빠뜨려 단독 처리)", [c]) for c in ids if c not in used
    ]

    out = []
    for title, reason, members in merged:
        evidences = [x.model_dump() for c in members for x in ids[c][1].evidences]
        summary = " / ".join(ids[c][1].summary for c in members)
        hints = [h for c in members for h in ids[c][1].link_hints]
        out.append(
            {
                "experience": {
                    "title": title,
                    "summary": summary,
                    "link_hints": hints,
                    "evidences": evidences,
                },
                "link_reason": reason,
                "candidates": members,
            }
        )
    return out


def _annotate(results: list[dict], chunks: list[dict]) -> dict:
    chunks_by_id = {c["id"]: c for c in chunks}
    doc_chunks: dict[str, list] = {}
    for c in chunks:
        doc_chunks.setdefault(c["document_id"], []).append(c)
    counts = {"ok": 0, "wrong_chunk": 0, "not_found": 0, "empty": 0}
    for r in results:
        for ev in r["experience"]["evidences"]:
            status = _check(Evidence(**ev), chunks_by_id, doc_chunks)
            ev["check"] = status
            ev["location"] = (chunks_by_id.get(ev["chunk_id"]) or {}).get("location", "?")
            counts[status] += 1
        r["documents"] = sorted(
            {ev["chunk_id"].split(":")[0] for ev in r["experience"]["evidences"]}
        )
    return counts


def _render(name: str, run: dict, labels: dict) -> list[str]:
    lines = [f"## {name}", ""]
    multi = sum(len(r["documents"]) > 1 for r in run["results"])
    lines.append(
        f"- 경험 {len(run['results'])}개 (여러 문서에 걸친 것 {multi}개) | 인용 검사 {run['checks']} | "
        f"LLM {run['usage']['calls']}회, 입력 {run['usage']['input']} / 출력 {run['usage']['output']} (추론 {run['usage']['reasoning']}) 토큰, "
        f"{run['seconds']:.0f}초"
    )
    for doc in run.get("inventory") or []:
        lines.append(f"- 사건 목록 {doc['document_id']}:")
        for ev in doc["events"]:
            mark = "경험" if ev["is_experience"] else "제외"
            lines.append(f"  - [{mark}] {ev['summary']} — {ev['reason']}")
    for n, r in enumerate(run["results"], 1):
        e = r["experience"]
        docs = ", ".join(r["documents"])
        lines += ["", f"### {n}. {e['title']}  [{docs}]", f"{e['summary']}"]
        if r.get("link_reason"):
            lines.append(f"- 연결 근거({', '.join(r.get('candidates', []))}): {r['link_reason']}")
        for ev in e["evidences"]:
            mark = "✓" if ev["check"] == "ok" else f"✗{ev['check']}"
            lines.append(
                f"- {mark} [{ev['kind']}] {ev['chunk_id']} ({ev['location']}): {ev['statement']}"
            )
            lines.append(f"  > {ev['quote']}")
    return lines + [""]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--mask", action="store_true", help="원본에 더해 가린 입력으로도 돌린다")
    parser.add_argument("--out", default=None)
    parser.add_argument("--methods", default="A,B", help="쉼표로 구분: A, A2, B")
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--reasoning", default=None, help="추론 모델의 reasoning_effort (예: low)")
    args = parser.parse_args()
    _MODEL.update(name=args.model, reasoning=args.reasoning)
    methods = {
        "A": ("A 통째", run_all),
        "A2": ("A2 통째+문서별 사건 목록", run_all2),
        "B": ("B 문서별+연결", run_per_doc),
    }

    folder = Path(args.project).expanduser()
    out = Path(args.out or f"runs/compare/{folder.name}-{datetime.now():%Y%m%d-%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)

    report = [
        f"# 추출 방식 비교: {folder}",
        "",
        f"모델 {args.model}, "
        + (f"reasoning_effort {args.reasoning}" if args.reasoning else "temperature 0"),
        "",
    ]
    dump = {}
    for masked in [False, True] if args.mask else [False]:
        chunks, labels = _load(folder, masked)
        condition = "가림" if masked else "원본"
        report += [f"# 조건: {condition}", ""]
        report += [f"- {d}: {label}" for d, label in labels.items()] + [""]
        for method, fn in [methods[m.strip()] for m in args.methods.split(",")]:
            run_id = f"{condition}-{method}"
            _usage.update(calls=0, input=0, output=0, reasoning=0)
            started = time.perf_counter()
            output = fn(chunks, labels)
            results, inventory = output if isinstance(output, tuple) else (output, None)
            run = {
                "results": results,
                "checks": _annotate(results, chunks),
                "seconds": time.perf_counter() - started,
                "usage": dict(_usage),
                "inventory": inventory,
            }
            dump[run_id] = run
            report += _render(f"{condition} / {method}", run, labels)
            print(f"{run_id}: 경험 {len(results)}개, 인용 {run['checks']}", flush=True)
        if masked:
            (out / "masked_input.md").write_text(_format(chunks, labels), encoding="utf-8")

    (out / "report.md").write_text("\n".join(report), encoding="utf-8")
    (out / "results.json").write_text(
        json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"결과: {out}")


if __name__ == "__main__":
    main()
