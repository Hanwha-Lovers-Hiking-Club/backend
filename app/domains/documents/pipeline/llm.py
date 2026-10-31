# LLM 클라이언트 생성을 한 곳에 모은다 (모델 교체, temperature 등 설정 관리).
# 지금은 OpenAI 키로 쓰고, 나중에 해커톤 측에서 Claude/OpenAI 키를 받으면 이 함수만 고치면 된다.
# 임베딩(kure-v3) 클라이언트는 여기가 아니라 embed 노드 단계에서 따로 정한다.
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import settings
from app.domains.documents.pipeline.observe import LlmUsageCallback
from app.domains.documents.pipeline.retry import retry_sync

MODEL_NAME = "gpt-4.1-mini"
# 한 호출이 멈춘 채 응답이 없으면 job 전체가 멈춘다. SDK 기본값(10분)은 너무 길어서 줄인다.
# ponytail: SDK가 타임아웃도 2회 재시도하므로 한 호출의 최악은 이 값의 3배. 입력이 아주 큰 extract가
# 이 값을 넘기면 늘린다
LLM_TIMEOUT_SECONDS = 120


@lru_cache
def get_llm() -> BaseChatModel:
    return ChatOpenAI(
        model=MODEL_NAME,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
        timeout=LLM_TIMEOUT_SECONDS,
        callbacks=[LlmUsageCallback()],
    )


def invoke_with_retry(llm, messages):
    # 응답 형식 오류(스키마 위반, 파싱 실패: ValueError 계열)만 1회 즉시 재시도한다.
    # 전송 오류는 SDK가 이미 재시도하므로 여기서 또 하면 곱해진다.
    # 거부(refusal)나 잘림(length)은 다시 해도 같아서 제외.
    return retry_sync(
        lambda: llm.invoke(messages),
        retries=1,
        backoff=0,
        retry_on=(ValueError,),
        name="LLM 응답 형식",
    )
