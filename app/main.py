# FastAPI 앱 생성 + 모든 도메인의 router를 여기에 모아서 등록하는 곳.
# 새 도메인을 추가하면 이 파일에 import 한 줄, include_router 한 줄만 추가하면 된다.
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.domains.agent.router import router as agent_router
from app.domains.documents.router import router as documents_router
from app.domains.knowledge_cards.router import router as knowledge_cards_router

app = FastAPI(title="backend")

# CORS_ORIGINS는 쉼표로 구분된 문자열이라 나눠서 넘긴다 (빈 값은 제외)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
app.include_router(documents_router)
app.include_router(knowledge_cards_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
