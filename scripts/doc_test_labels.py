"""결과 문장의 상태 라벨(confirmed/expected/planned/unknown) 정확도를 사람이 눈으로 확인하는 시험.

문서 전체를 LLM에 넣고 "결과·효과를 말하는 문장"마다 원문 인용과 상태 라벨을 달게 한다.
DB에 아무것도 쓰지 않고 OpenAI만 호출한다. 결과는 표준출력(마크다운)으로 나온다.

사용법 (레포 루트에서):
    uv run python scripts/doc_test_labels.py --root "<데이터셋 폴더>" --project vigilantis \
        --docs "docs/adr/0004-rollback-runbook-whitelist-registration.md"
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doc_test_samples import SAMPLES  # noqa: E402
from doc_test_stage1 import resolve  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from app.domains.documents.pipeline.llm import get_llm, invoke_with_retry  # noqa: E402
from app.domains.documents.pipeline.models import OutcomeStatements  # noqa: E402
from app.domains.documents.pipeline.outcome_gate import _LABEL_PROMPT  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.environ.get("DOC_TEST_ROOT"))
    parser.add_argument("--project", required=True, choices=sorted(SAMPLES))
    parser.add_argument("--docs", required=True)
    args = parser.parse_args()
    project_dir = next(iter(Path(args.root).glob(f"{args.project}-*-dataset")))

    llm = get_llm().with_structured_output(OutcomeStatements)
    for spec in [s for s in args.docs.split(",") if s]:
        path = resolve(project_dir, spec)
        text = path.read_text(encoding="utf-8")
        result = invoke_with_retry(llm, [SystemMessage(_LABEL_PROMPT), HumanMessage(text)])
        print(f"\n## {path.name}\n")
        for s in result.statements:
            print(f"- [{s.status}] {s.quote[:160]}")


if __name__ == "__main__":
    main()
