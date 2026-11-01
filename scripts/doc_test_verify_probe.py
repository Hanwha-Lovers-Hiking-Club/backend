"""검증(verify) 노드가 틀린 카드를 잡는지 시험한다: 진짜 카드를 일부러 변조해서 넣어 본다.

DB를 건드리지 않고 OpenAI만 호출한다. 프롬프트나 검증 로직을 바꿀 때마다 돌려서
"변조 카드가 pass로 통과하는가"를 숫자로 확인하는 회귀 시험이다(기대: V0 외에는 rewrite/discard).
카드는 runs/stage3-md/all_cards.json(3단계 실행 결과)에서 읽고,
원문은 데이터셋에서 읽어 청크로 준다.

사용법: uv run python scripts/doc_test_verify_probe.py --root "<데이터셋 폴더>"
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import setup_logging  # noqa: E402
from app.domains.documents.pipeline.models import CardDraft, CardEvidenceDraft  # noqa: E402
from app.domains.documents.pipeline.nodes.chunk import chunk  # noqa: E402
from app.domains.documents.pipeline.nodes.verify import verify  # noqa: E402

FIELDS = [
    "title", "summary", "context", "problem", "cause", "approach", "rationale", "outcome",
    "lesson", "technologies", "keywords", "project_name", "department",
]  # fmt: skip


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.environ.get("DOC_TEST_ROOT"))
    parser.add_argument("--cards", default="runs/stage3-md/all_cards.json")
    args = parser.parse_args()
    setup_logging()

    cards = json.load(open(args.cards, encoding="utf-8"))
    base = next(c for c in cards if c["_project"] == "vigilantis" and "롤백 런북" in c["title"])
    document_id = base["card_documents"][0]["document_id"]
    source = glob.glob(
        f"{args.root}/vigilantis-*/original_markdown/docs/adr/0004-rollback-runbook-whitelist-registration.md"
    )[0]
    chunks = chunk(
        {
            "parsed_documents": [
                {"document_id": document_id, "text": open(source, encoding="utf-8").read()}
            ]
        }
    )["chunks"]

    def draft(**over) -> CardDraft:
        fields = {k: base[k] for k in FIELDS} | {"project_date": None} | over
        evidences = [
            CardEvidenceDraft(
                document_id=e["document_id"], location=e["location"], excerpt=e["excerpt"]
            )
            for e in base["card_documents"]
        ]
        return CardDraft(**fields, evidences=evidences)

    variants = {
        "V0 원본 그대로 (기준: 시제 왜곡이 있던 카드)": draft(),
        "V1 outcome에 지어낸 수치": draft(
            outcome="배포 이후 가드레일 오차단율이 40% 감소하고 원복이 12초에서 3초로 단축됨"
        ),
        "V2 지어낸 원인": draft(
            cause="Redis 캐시의 TTL 설정 오류로 Whitelist 조회가 실패했기 때문"
        ),
        "V3 미확인 결과를 실적처럼 서술": draft(
            outcome="2026-09-01 프로덕션 배포 후 한 달간 무장애로 운영되었고 관제자 만족도가 높았음"
        ),
        "V4 원문과 반대되는 결정": draft(
            approach="롤백 런북은 Whitelist 검증을 우회하여 즉시 실행되도록 처리함",
            rationale="긴급 원복이 항상 실행되도록 하기 위해",
        ),
    }
    out = verify({"card_drafts": list(variants.values()), "chunks": chunks})["verified_cards"]
    caught = 0
    for (name, _), v in zip(variants.items(), out, strict=True):
        caught += name[:2] != "V0" and v.verification.verdict != "pass"
        print(f"\n{name}\n  판정: {v.verification.verdict}\n  사유: {v.verification.reason[:300]}")
    print(f"\n변조 카드(V1~V4) 중 걸러낸 수: {caught}/4")


if __name__ == "__main__":
    main()
