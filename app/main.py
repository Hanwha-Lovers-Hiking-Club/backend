# FastAPI 앱 생성 + 모든 도메인의 router를 여기에 모아서 등록하는 곳.
# 새 도메인을 추가하면 이 파일에 import 한 줄, include_router 한 줄만 추가하면 된다.
# 도메인 API는 전부 /api 아래에 둔다 (include_router의 prefix="/api"). /health만 예외.
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.logging import setup_logging
from app.domains.agent.router import router as agent_router
from app.domains.chat.router import router as chat_router
from app.domains.documents.router import router as documents_router
from app.domains.knowledge_cards.router import router as knowledge_cards_router

setup_logging()

app = FastAPI(title="backend")

# CORS_ORIGINS는 쉼표로 구분된 문자열이라 나눠서 넘긴다 (빈 값은 제외)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(knowledge_cards_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
