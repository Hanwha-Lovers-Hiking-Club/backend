# 테스트 공통 설정. app/config.py와 app/db.py는 import 시점에 settings와 supabase 클라이언트를
# 만들기 때문에, app을 import하기 전에 더미 환경변수를 먼저 넣는다.
# 테스트는 실제 Supabase를 호출하지 않는다.
import os

os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-service-role-key"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"
os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["KURE_API_KEY"] = "test-kure-key"
os.environ["CHAT_VERIFY_ENABLED"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def anyio_backend():
    # async 테스트는 anyio 플러그인(starlette 의존성으로 이미 설치됨)으로 돌리고, asyncio만 쓴다
    return "asyncio"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def make_card():
    # 챗봇 테스트용 지식카드(KnowledgeCard) 팩토리. 카드마다 id와 근거 id가 다르다
    from uuid import uuid4

    from app.domains.knowledge_cards.schemas import KnowledgeCard

    def factory(title: str = "RAG 검색 정확도 개선", **overrides) -> KnowledgeCard:
        data = {
            "id": uuid4(),
            "title": title,
            "summary": "하이브리드 검색 도입 사례",
            "context": "벡터 검색만으로 정확한 기술 용어 검색이 어려웠다.",
            "problem": "특정 키워드 검색 실패",
            "cause": None,
            "approach": "키워드 검색과 벡터 검색 결합",
            "rationale": None,
            "outcome": None,
            "lesson": "검색 유형에 따라 검색 방식을 결합해야 한다.",
            "technologies": ["Supabase", "pgvector"],
            "keywords": ["RAG"],
            "status": "active",
            "quality_score": None,
            "last_verified_at": None,
            "verification_version": None,
            "project_name": "AI Knowledge Platform",
            "department": None,
            "project_date": None,
            "created_at": "2026-10-20T09:00:00+00:00",
            "updated_at": "2026-10-24T09:00:00+00:00",
            "evidences": [
                {
                    "id": uuid4(),
                    "document_id": uuid4(),
                    "document_title": "검색 시스템 설계서",
                    "original_filename": "search-design.pdf",
                    "location": "3장 검색 방식",
                    "excerpt": "벡터 검색과 키워드 검색을 결합한다.",
                }
            ],
            **overrides,
        }
        return KnowledgeCard.model_validate(data)

    return factory
