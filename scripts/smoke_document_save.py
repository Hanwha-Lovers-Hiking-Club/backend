"""Run after the save RPC is installed: uv run python -m scripts.smoke_document_save --run.

Creates uniquely named test rows, checks RPC behavior, then removes the rows.
This is an opt-in write test against the configured Supabase project.
"""

import argparse
import json
from urllib.request import Request, urlopen
from uuid import uuid4

from postgrest.exceptions import APIError

from app.config import settings
from app.db import supabase


def require_rpc() -> None:
    key = settings.SUPABASE_SERVICE_ROLE_KEY
    request = Request(
        settings.SUPABASE_URL.rstrip("/") + "/rest/v1/",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/openapi+json",
        },
    )
    with urlopen(request, timeout=15) as response:
        schema = json.load(response)
    if "document_save_receipts" not in schema.get(
        "definitions", {}
    ) or "/rpc/save_document_cards" not in schema.get("paths", {}):
        raise RuntimeError(
            "Apply the receipt table and save_document_cards RPC before this smoke test"
        )


def card(title: str, *, evidence=True) -> dict:
    vector = [1.0] + [0.0] * 1023
    return {
        "title": title,
        "summary": "Temporary save smoke test",
        "context": "Verify atomic document card persistence",
        "problem": "Save contract needs a database check",
        "cause": None,
        "approach": None,
        "rationale": None,
        "outcome": None,
        "lesson": "Confirm the save transaction before integration",
        "technologies": [],
        "keywords": [],
        "evidences": [{"location": "smoke:1", "excerpt": "temporary test evidence"}]
        if evidence
        else [],
        "content_embedding": vector,
        "situation_embedding": vector,
        "solution_embedding": vector,
        "lesson_embedding": vector,
    }


def call_save(document_id, payload_hash, cards):
    return (
        supabase.rpc(
            "save_document_cards",
            {
                "p_document_id": str(document_id),
                "p_payload_hash": payload_hash,
                "p_cards": cards,
            },
        )
        .execute()
        .data
    )


def must_reject(document_id, payload_hash, cards):
    try:
        call_save(document_id, payload_hash, cards)
    except APIError:
        return
    raise AssertionError("The database accepted a save that should have rolled back")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="write temporary rows to Supabase")
    args = parser.parse_args()
    if not args.run:
        parser.error("pass --run to execute the database write test")
    try:
        require_rpc()
    except RuntimeError as exc:
        parser.exit(2, f"{exc}\n")

    marker = "codex-save-smoke-" + uuid4().hex
    document_ids = [uuid4() for _ in range(3)]
    titles = [marker + suffix for suffix in ("-saved", "-rollback", "-zero")]
    try:
        for document_id in document_ids:
            supabase.table("documents").insert(
                {
                    "id": str(document_id),
                    "title": marker + ".txt",
                    "original_filename": marker + ".txt",
                    "size_bytes": 1,
                    "storage_bucket": settings.SUPABASE_STORAGE_BUCKET,
                    "storage_path": "smoke/" + str(document_id) + ".txt",
                }
            ).execute()

        saved = call_save(document_ids[0], "a" * 64, [card(titles[0])])
        assert saved["committed"] is True and len(saved["card_ids"]) == 1
        repeated = call_save(document_ids[0], "a" * 64, [card(titles[0])])
        assert repeated["card_ids"] == saved["card_ids"]
        must_reject(document_ids[0], "b" * 64, [card(titles[0])])
        rows = (
            supabase.table("knowledge_cards")
            .select(
                "id,status,content_embedding,situation_embedding,solution_embedding,lesson_embedding"
            )
            .eq("id", saved["card_ids"][0])
            .execute()
            .data
        )
        assert len(rows) == 1 and rows[0]["status"] == "active"
        assert all(
            len(
                json.loads(rows[0][view + "_embedding"])
                if isinstance(rows[0][view + "_embedding"], str)
                else rows[0][view + "_embedding"]
            )
            == 1024
            for view in ("content", "situation", "solution", "lesson")
        )
        evidences = (
            supabase.table("card_documents")
            .select("id")
            .eq("card_id", saved["card_ids"][0])
            .execute()
            .data
        )
        assert len(evidences) == 1

        must_reject(
            document_ids[1],
            "c" * 64,
            [card(titles[1]), card(titles[1] + "-invalid", evidence=False)],
        )
        assert not (
            supabase.table("knowledge_cards").select("id").eq("title", titles[1]).execute().data
        )
        assert not (
            supabase.table("document_save_receipts")
            .select("document_id")
            .eq("document_id", str(document_ids[1]))
            .execute()
            .data
        )
        assert not (
            supabase.table("card_documents")
            .select("id")
            .eq("document_id", str(document_ids[1]))
            .execute()
            .data
        )

        zero = call_save(document_ids[2], "d" * 64, [])
        assert zero["card_ids"] == []
        assert call_save(document_ids[2], "d" * 64, [])["card_ids"] == []
        must_reject(document_ids[2], "e" * 64, [])
        print("save, idempotency, rejection, rollback and zero-card checks passed")
    finally:
        # A document delete cascades to evidence/receipt, but cards need explicit cleanup.
        for title in (titles[0], titles[1], titles[1] + "-invalid", titles[2]):
            supabase.table("knowledge_cards").delete().eq("title", title).execute()
        for document_id in document_ids:
            supabase.table("documents").delete().eq("id", str(document_id)).execute()


if __name__ == "__main__":
    main()
