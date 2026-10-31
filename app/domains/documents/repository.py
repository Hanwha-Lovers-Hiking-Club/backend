"""Supabase Storage and durable document job persistence."""

import asyncio
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.db import supabase


def _bucket():
    return supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET)


async def upload_file(path: str, local_path: Path, content_type: str) -> None:
    await asyncio.to_thread(
        _bucket().upload, path, local_path, {"content-type": content_type, "upsert": "false"}
    )


async def remove_file(path: str) -> None:
    await asyncio.to_thread(_bucket().remove, [path])


async def download_file(path: str) -> bytes:
    return await asyncio.to_thread(_bucket().download, path)


async def create_document(document_id: UUID, filename: str, size: int, path: str) -> None:
    row = {
        "id": str(document_id),
        "title": filename,
        "original_filename": filename,
        "size_bytes": size,
        "storage_bucket": settings.SUPABASE_STORAGE_BUCKET,
        "storage_path": path,
    }
    await asyncio.to_thread(lambda: supabase.table("documents").insert(row).execute())


async def delete_document(document_id: UUID) -> None:
    await asyncio.to_thread(
        lambda: supabase.table("documents").delete().eq("id", str(document_id)).execute()
    )


async def create_job(job_id: UUID, document_ids: list[UUID]) -> None:
    await asyncio.to_thread(
        lambda: supabase.rpc(
            "create_document_job",
            {"p_job_id": str(job_id), "p_document_ids": [str(item) for item in document_ids]},
        ).execute()
    )


async def check_job_store() -> None:
    await asyncio.to_thread(lambda: supabase.table("document_jobs").select("id").limit(0).execute())


async def record_event(job_id: UUID, event_type: str, payload: dict, status: str | None = None):
    response = await asyncio.to_thread(
        lambda: supabase.rpc(
            "record_document_job_event",
            {
                "p_job_id": str(job_id),
                "p_event_type": event_type,
                "p_payload": payload,
                "p_status": status,
            },
        ).execute()
    )
    return response.data


async def get_job(job_id: UUID) -> dict | None:
    response = await asyncio.to_thread(
        lambda: (
            supabase.table("document_jobs")
            .select("id,status,document_ids,result,created_at,updated_at")
            .eq("id", str(job_id))
            .limit(1)
            .execute()
        )
    )
    return response.data[0] if response.data else None


async def get_events(job_id: UUID, after_id: int = 0) -> list[dict]:
    response = await asyncio.to_thread(
        lambda: (
            supabase.table("document_job_events")
            .select("id,event_type,payload,created_at")
            .eq("job_id", str(job_id))
            .gt("id", after_id)
            .order("id")
            .limit(100)
            .execute()
        )
    )
    return response.data or []


async def get_documents(document_ids: list[UUID]) -> list[dict]:
    response = await asyncio.to_thread(
        lambda: (
            supabase.table("documents")
            .select("id,original_filename,storage_path")
            .in_("id", [str(item) for item in document_ids])
            .execute()
        )
    )
    return response.data or []
