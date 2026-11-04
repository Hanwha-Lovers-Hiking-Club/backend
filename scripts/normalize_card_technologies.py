"""기존 지식카드의 technologies를 표준 ID로 정규화한다 (일회성 + 반복 실행 가능).

별칭 사전(app/core/technology/aliases.py)을 고친 뒤에도 다시 돌리면 기존 카드에 반영된다.
이미 정규화된 카드는 바뀌지 않으므로(멱등) 몇 번 돌려도 안전하다.

사용법 (레포 루트에서):
    uv run python scripts/normalize_card_technologies.py            # 미리보기만, DB는 안 바꿈
    uv run python scripts/normalize_card_technologies.py --apply    # 실제로 반영
    uv run python scripts/normalize_card_technologies.py --apply --backup before.json
        # 반영 전 값을 JSON으로 저장해 둔다 (되돌릴 때 이 값으로 technologies를 복원)
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.technology import normalize_technologies  # noqa: E402
from app.db import supabase  # noqa: E402

PAGE_SIZE = 500


def fetch_cards() -> list[dict]:
    cards: list[dict] = []
    start = 0
    while True:
        page = (
            supabase.table("knowledge_cards")
            .select("id,title,technologies")
            .order("created_at")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
        )
        cards.extend(page)
        if len(page) < PAGE_SIZE:
            return cards
        start += PAGE_SIZE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제로 DB에 반영한다")
    parser.add_argument("--backup", help="반영 전 technologies를 저장할 JSON 파일")
    args = parser.parse_args()

    changes = []
    for card in fetch_cards():
        before = card["technologies"] or []
        after = normalize_technologies(before)
        if after != before:
            changes.append(
                {"id": card["id"], "title": card["title"], "before": before, "after": after}
            )

    for change in changes:
        print(f"{change['title'][:40]!r}: {change['before']} -> {change['after']}")
    print(
        f"\n변경 대상 {len(changes)}개 카드" + ("" if args.apply else " (미리보기: --apply로 반영)")
    )

    if not args.apply or not changes:
        return
    if args.backup:
        Path(args.backup).write_text(
            json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"반영 전 값을 {args.backup}에 저장")
    for change in changes:
        supabase.table("knowledge_cards").update({"technologies": change["after"]}).eq(
            "id", change["id"]
        ).execute()
    print(f"{len(changes)}개 카드 반영 완료")


if __name__ == "__main__":
    main()
