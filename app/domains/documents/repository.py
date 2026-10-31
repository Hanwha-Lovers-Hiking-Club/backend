# documents 테이블 등 이 도메인이 쓰는 Supabase 호출을 모아두는 곳.
# 노드/서비스가 DB를 직접 만지지 않고 여기 함수를 거치게 한다.
# 지식카드 저장은 이 파일이 아니라 knowledge_cards 도메인의 service를 호출한다.
from app.config import settings
from app.db import supabase


def upload_file(path: str, content: bytes, content_type: str) -> None:
    supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
        path, content, {"content-type": content_type}
    )


def download_file(path: str) -> bytes:
    return supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(path)


def remove_files(paths: list[str]) -> None:
    supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).remove(paths)


def insert_documents(rows: list[dict]) -> list[str]:
    # 넣은 순서대로 생성된 documents.id를 돌려준다
    result = supabase.table("documents").insert(rows).execute()
    return [row["id"] for row in result.data]


def insert_chunks(rows: list[dict]) -> None:
    supabase.table("document_chunks").insert(rows).execute()


def delete_chunks(document_ids: list[str]) -> None:
    supabase.table("document_chunks").delete().in_("document_id", document_ids).execute()
