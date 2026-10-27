# LLM 클라이언트 생성을 한 곳에 모은다 (모델 교체, temperature 등 설정 관리).
# 지금은 OpenAI 키로 쓰고, 나중에 해커톤 측에서 Claude/OpenAI 키를 받으면 이 함수만 고치면 된다.
# 임베딩(kure-v3) 클라이언트는 여기가 아니라 embed 노드 단계에서 따로 정한다.
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import settings

MODEL_NAME = "gpt-4.1-mini"


@lru_cache
def get_llm() -> BaseChatModel:
    return ChatOpenAI(model=MODEL_NAME, api_key=settings.OPENAI_API_KEY, temperature=0)
