# documents 테이블·Storage·작업 이력 등 이 도메인이 쓰는 Supabase 호출을 모아두는 곳.
# 노드/서비스가 DB를 직접 만지지 않고 여기 함수를 거치게 한다.
# 지식카드 저장은 이 파일이 아니라 knowledge_cards 도메인의 service를 호출한다.
# async 함수는 업로드·작업 실행용(동기 supabase 호출을 스레드에서 돌림), 동기 함수는 원본 URL과
# 원문 검색 청크용이다(호출하는 쪽이 스레드에서 부른다).
import asyncio
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.core.text import strip_nul
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


async def expire_stale_job(job_id: UUID) -> bool:
    response = await asyncio.to_thread(
        lambda: supabase.rpc("expire_stale_document_job", {"p_job_id": str(job_id)}).execute()
    )
    return bool(response.data)


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


def insert_chunks(rows: list[dict]) -> None:
    supabase.table("document_chunks").insert(strip_nul(rows)).execute()


def delete_chunks(document_ids: list[str]) -> None:
    supabase.table("document_chunks").delete().in_("document_id", document_ids).execute()


def get_document(document_id: str) -> dict | None:
    # 없는 id일 수 있어서 .single() 대신 리스트로 받아 직접 확인한다
    result = (
        supabase.table("documents")
        .select("storage_path, original_filename")
        .eq("id", document_id)
        .execute()
    )
    return result.data[0] if result.data else None


def create_signed_url(path: str, expires_in: int, download: str | None = None) -> str:
    # download에 파일명을 주면 브라우저가 열지 않고 그 이름으로 내려받는다
    options = {"download": download} if download else None
    result = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).create_signed_url(
        path, expires_in, options
    )
    return result["signedURL"]
