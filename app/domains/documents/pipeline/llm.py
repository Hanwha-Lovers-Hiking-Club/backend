# LLM 클라이언트 생성을 한 곳에 모은다 (모델, 추론 강도, 타임아웃 설정 관리).
# 지금은 OpenAI 키로 쓰고, 나중에 해커톤 측에서 Claude/OpenAI 키를 받으면 이 함수만 고치면 된다.
# 임베딩(kure-v3) 클라이언트는 여기가 아니라 embed 노드 단계에서 따로 정한다.
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import settings
from app.domains.documents.pipeline.observe import LlmUsageCallback
from app.domains.documents.pipeline.retry import retry_sync

# 모델은 비교 실험으로 정했다: 같은 입력에서 gpt-4.1-mini/gpt-4.1은
# 문서 하나에만 있는 경험을 자주 빠뜨렸고, gpt-5.6-terra는 빠뜨림·잘못 묶기·지어낸 인용이 없었다.
MODEL_NAME = "gpt-5.6-terra"
# 추론 강도. low에서 추론 토큰은 호출당 수백 개 수준이었다(실험 실측)
REASONING_EFFORT = "low"
# 한 호출이 멈춘 채 응답이 없으면 job 전체가 멈춘다. SDK 기본값(10분)은 너무 길어서 줄인다.
# ponytail: SDK가 타임아웃도 2회 재시도하므로 한 호출의 최악은 이 값의 3배. 입력이 큰 호출이
# 이 값을 넘기면 늘린다
LLM_TIMEOUT_SECONDS = 120


@lru_cache
def get_llm() -> BaseChatModel:
    return ChatOpenAI(
        model=MODEL_NAME,
        api_key=settings.OPENAI_API_KEY,
        # 추론 모델은 temperature를 고정할 수 없어서 넘기지 않는다
        reasoning_effort=REASONING_EFFORT,
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
