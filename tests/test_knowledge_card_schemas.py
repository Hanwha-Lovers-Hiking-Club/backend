from app.domains.knowledge_cards.schemas import KnowledgeCard

CARD = {
    "id": "8d482ad2-63a9-42f4-a9fb-fab341ef6d25",
    "title": "RAG 검색 정확도 개선",
    "summary": "하이브리드 검색 도입 사례",
    "context": "벡터 검색만으로 정확한 기술 용어 검색이 어려웠다.",
    "problem": "특정 키워드 검색 실패",
    "cause": None,
    "approach": None,
    "rationale": None,
    "outcome": None,
    "lesson": "검색 유형에 따라 검색 방식을 결합해야 한다.",
    "technologies": ["Supabase"],
    "keywords": ["RAG"],
    "status": "active",
    "quality_score": 0.92,
    "last_verified_at": None,
    "verification_version": None,
    "project_name": "AI Knowledge Platform",
    "department": None,
    "project_date": "2026-10-20",
    "created_at": "2026-10-20T09:00:00Z",
    "updated_at": "2026-10-24T09:00:00Z",
    "evidences": [
        {
            "id": "0b9a5c1e-1f0d-4d4b-9a51-3c7a3f0e8b21",
            "document_id": "4c10a229-9b3a-40bc-8611-e2f787540f03",
            "document_title": "검색 시스템 설계서",
            "original_filename": "search-design.pdf",
            "location": "3장 검색 방식",
            "excerpt": "벡터 검색과 키워드 검색을 결합한다.",
        }
    ],
}


def test_knowledge_card_serializes_to_camel_case():
    data = KnowledgeCard.model_validate(CARD).model_dump(mode="json", by_alias=True)

    assert data["qualityScore"] == 0.92
    assert data["projectName"] == "AI Knowledge Platform"
    assert data["projectDate"] == "2026-10-20"
    assert "lastVerifiedAt" in data
    assert "quality_score" not in data
    assert data["evidences"][0] == {
        "id": "0b9a5c1e-1f0d-4d4b-9a51-3c7a3f0e8b21",
        "documentId": "4c10a229-9b3a-40bc-8611-e2f787540f03",
        "documentTitle": "검색 시스템 설계서",
        "originalFilename": "search-design.pdf",
        "location": "3장 검색 방식",
        "excerpt": "벡터 검색과 키워드 검색을 결합한다.",
        "url": None,
    }
