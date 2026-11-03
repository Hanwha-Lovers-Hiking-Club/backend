# 기본 RAG 문서 도메인의 DB 호출을 모은다 (원본 문서 조회, baseline_chunks 삭제/저장).
# 노드와 서비스는 DB를 직접 만지지 않고 이 파일의 함수를 거친다.
# Storage 파일 다운로드는 main의 documents/repository.py를 그대로 쓴다.
from app.db import supabase


def get_documents(document_ids: list[str]) -> list[dict]:
    # main의 get_document는 title을 조회하지 않아서 따로 둔다 (title은 검색용 텍스트의 헤더에 쓴다).
    # 없는 id는 결과에서 빠진다. 어떤 id가 없는지는 호출한 쪽에서 확인한다
    result = (
        supabase.table("documents")
        .select("id, title, storage_path, original_filename")
        .in_("id", document_ids)
        .execute()
    )
    return result.data


def replace_chunks(document_id: str, rows: list[dict]) -> None:
    # 다시 인덱싱할 때 이전 청크가 남지 않도록 지우고 새로 넣는다.
    # 두 호출이 한 트랜잭션은 아니라서, insert가 실패하면 그 문서의 청크가 비어 있을 수 있다.
    # 그때는 같은 문서를 다시 인덱싱하면 된다
    supabase.table("baseline_chunks").delete().eq("document_id", document_id).execute()
    if rows:
        supabase.table("baseline_chunks").insert(rows).execute()
