# ruff: noqa: E501 — 리포트 줄을 만드는 f-string이 길다(개발용 스크립트)
"""verify 노드 평가: dry-run 결과(state)의 카드 후보를 일부러 변조해서, 검증기가 기대한 대로 처리하는지 본다.

원본 카드의 판정은 dry-run이 이미 낸 것을 그대로 쓰고(다시 호출하지 않음), 변조 카드와 짝 시험만 새로 검증한다.
사례마다 기대 처리(판정, 비워야 할 필드)를 적어 두고 실제 결과와 비교한다. DB에 쓰지 않고 OpenAI만 호출한다.
알려진 경계 사례로 프롬프트를 고쳤다면 최종 평가는 튜닝에 쓰지 않은 프로젝트로도 해야 한다.

사용법 (레포 루트에서):
    uv run python scripts/verify_eval.py runs/dry/<실행 폴더> [--per-type 2]
결과: <실행 폴더>/verify_eval.md
"""

import argparse
import pickle
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import setup_logging  # noqa: E402
from app.domains.documents.pipeline.llm import get_llm  # noqa: E402
from app.domains.documents.pipeline.models import CardDraft, ClaimChecks  # noqa: E402
from app.domains.documents.pipeline.nodes.verify import verify_one  # noqa: E402

FAKE_NUMBER = " 처리 시간도 평균 37% 단축됐다."
OVERREACH = " 이 방식을 적용하면 같은 유형의 문제를 모든 환경에서 완전히 방지할 수 있다."


@dataclass
class Case:
    name: str
    draft: CardDraft
    experience_index: int | None
    verdicts: set[str]  # 허용하는 판정
    cleared: set[str]  # 반드시 비워져야 하는 필드
    intact: set[str]  # 비워지면 안 되는 필드


def _cases(state: dict, per_type: int) -> list[Case]:
    experiences = state.get("experiences", [])
    cases: list[Case] = []
    counts: dict[str, int] = {}

    def add(kind: str, case: Case) -> None:
        if counts.get(kind, 0) < per_type:
            counts[kind] = counts.get(kind, 0) + 1
            cases.append(case)

    for d in state.get("card_drafts", []):
        exp = experiences[d.experience_index] if d.experience_index is not None else None
        if d.outcome:
            # 가짜 수치가 outcome에만 있으면 outcome만 비우고 카드는 살리는 게 정답
            add(
                "fake_outcome",
                Case(
                    f"가짜 수치(outcome만) | {d.title}",
                    d.model_copy(update={"outcome": d.outcome + FAKE_NUMBER}),
                    d.experience_index,
                    {"passed"},
                    {"outcome"},
                    {"title", "summary"},
                ),
            )
            # 같은 가짜 수치가 summary에도 있으면 필수 필드 문제라 보류가 정답
            add(
                "fake_outcome_summary",
                Case(
                    f"가짜 수치(outcome+summary) | {d.title}",
                    d.model_copy(
                        update={
                            "outcome": d.outcome + FAKE_NUMBER,
                            "summary": d.summary + FAKE_NUMBER,
                        }
                    ),
                    d.experience_index,
                    {"held"},
                    {"outcome"},
                    set(),
                ),
            )
        add(
            "lesson_overreach",
            Case(
                f"lesson 과장 | {d.title}",
                d.model_copy(update={"lesson": d.lesson + OVERREACH}),
                d.experience_index,
                {"held"},
                set(),
                set(),
            ),
        )
        plans = [e for e in (exp.evidences if exp else []) if e.kind == "plan"]
        if plans and d.approach:
            # 계획을 결과로 쓴 outcome은 비워야 한다(다른 조건부 필드가 있어 카드는 남는다)
            add(
                "plan_as_outcome",
                Case(
                    f"계획을 outcome으로 | {d.title}",
                    d.model_copy(
                        update={
                            "outcome": f"{plans[0].statement} 이 조치가 완료되어 효과가 확인됐다."
                        }
                    ),
                    d.experience_index,
                    {"passed"},
                    {"outcome"},
                    {"approach"},
                ),
            )
        rejected = [e for e in (exp.evidences if exp else []) if e.kind == "rejected_hypothesis"]
        if rejected and d.approach:
            # 기각한 가설을 확정 원인으로 쓴 cause는 비워야 한다
            add(
                "rejected_as_cause",
                Case(
                    f"기각한 가설을 원인으로 | {d.title}",
                    d.model_copy(update={"cause": f"확인된 원인: {rejected[0].statement}"}),
                    d.experience_index,
                    {"passed"},
                    {"cause"},
                    {"approach"},
                ),
            )
    return cases


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("run_dir")
    parser.add_argument(
        "--per-type", type=int, default=2, help="변조 종류마다 만들 사례 수(비용 조절)"
    )
    parser.add_argument(
        "--pair",
        nargs=3,
        action="append",
        default=[],
        metavar=("제목일부", "과장제목", "제한제목"),
        help="짝 시험: 카드 제목 일부, 과장한 제목(기대: held), 범위를 제한한 제목(기대: passed)",
    )
    args = parser.parse_args()
    setup_logging()

    llm = get_llm().with_structured_output(ClaimChecks)
    lines = ["# verify 평가", ""]
    rows, failures = [], 0
    for pkl in sorted(Path(args.run_dir).glob("*.pkl")):
        state = pickle.loads(pkl.read_bytes())
        chunks = {c["id"]: c for c in state["chunks"]}
        names = {d["id"]: d["original_filename"] for d in state["documents"]}
        experiences = state.get("experiences", [])

        # 원본 카드: dry-run의 판정을 그대로 보여 준다(사람이 정상 카드인지 판단)
        for c in state.get("verified_cards", []):
            v = c.verification
            rows.append(
                f"| 원본 | {pkl.stem} | {c.draft.title[:40]} | (사람 판단) | {v.verdict}/{v.status} 비움 {v.cleared_fields} | - |"
            )

        cases = _cases(state, args.per_type)
        for part, overclaim, limited in args.pair:
            for d in state.get("card_drafts", []):
                if part in d.title:
                    cases.append(
                        Case(
                            f"짝-과장 | {d.title}",
                            d.model_copy(update={"title": overclaim}),
                            d.experience_index,
                            {"held"},
                            set(),
                            set(),
                        )
                    )
                    cases.append(
                        Case(
                            f"짝-제한 | {d.title}",
                            d.model_copy(update={"title": limited}),
                            d.experience_index,
                            {"passed"},
                            set(),
                            {"title"},
                        )
                    )
        for case in cases:
            exp = experiences[case.experience_index] if case.experience_index is not None else None
            try:
                result = verify_one(case.draft, exp, chunks, names, llm)
            except Exception as e:
                rows.append(
                    f"| {case.name.split(' | ')[0]} | {pkl.stem} | {case.name.split(' | ')[1][:40]} | {sorted(case.verdicts)} | 호출 실패 {e} | ✗ |"
                )
                failures += 1
                continue
            v = result.verification
            ok = (
                v.verdict in case.verdicts
                and case.cleared <= set(v.cleared_fields)
                and not (case.intact & set(v.cleared_fields))
            )
            failures += not ok
            kind, title = case.name.split(" | ", 1)
            rows.append(
                f"| {kind} | {pkl.stem} | {title[:40]} | {sorted(case.verdicts)} 비움 {sorted(case.cleared)} | {v.verdict}/{v.status} 비움 {v.cleared_fields} | {'✓' if ok else '✗'} |"
            )
            lines_reason = " / ".join(v.reasons)[:400]
            print(
                f"{'✓' if ok else '✗'} {case.name[:60]} -> {v.verdict} {v.cleared_fields} {lines_reason}",
                flush=True,
            )

    lines += ["| 사례 | 프로젝트 | 카드 | 기대 | 실제 | 일치 |", "|---|---|---|---|---|---|", *rows]
    lines += ["", f"변조·짝 사례 중 기대와 다른 것: {failures}개"]
    out = Path(args.run_dir) / "verify_eval.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"결과: {out}")


if __name__ == "__main__":
    main()
