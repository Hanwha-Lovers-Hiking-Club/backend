# LLM 클라이언트 생성을 한 곳에 모은다 (모델, 추론 강도, 타임아웃 설정 관리).
# 지금은 OpenAI 키로 쓰고, 나중에 해커톤 측에서 Claude/OpenAI 키를 받으면 이 함수만 고치면 된다.
# 임베딩(kure-v3) 클라이언트는 여기가 아니라 embed 노드 단계에서 따로 정한다.
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import settings
from app.domains.documents.pipeline.observe import LlmUsageCallback
from app.domains.documents.pipeline.retry import retry_sync

# 문서 파이프라인 모델은 비교 실험으로 정했다: 같은 입력에서 gpt-4.1-mini/gpt-4.1은
# 문서 하나에만 있는 경험을 자주 빠뜨렸고, gpt-5.6-terra는 빠뜨림·잘못 묶기·지어낸 인용이 없었다.
# gpt-6.1-terra는 같은 문서 dry-run에서 경험·카드 수와 판정이 같고 지어낸 인용이 더 적었으며,
# 단가가 절반 이하다(2026-11). 교훈 문장이 약해지는 경향은 draft_card 프롬프트로 보완했다
MODEL_NAME = "gpt-6.1-terra"
# 챗봇(chat 도메인)은 파이프라인 모델을 바꿀 때 함께 바뀌지 않도록 따로 둔다.
# 같은 질문 11개 x 2회 비교(2026-11-09)에서 6.1은 5.6과 응답 시간·의도 분류·참조 카드가 같았고,
# 자료 없는 질문에 더 일관되게 "자료 없음"으로 답했다. 6.1은 "사실 근거는 원문" 규칙을 엄격히 지켜
# 원문이 안 걸린 카드의 사례·수치를 빼서 답이 짧았다(길이 중앙값 5.6 827자, 6.1 677자).
# answer.md에서 원문에 없으면 카드 내용도 근거로 쓰게 바꾼 뒤 852자, 근거에 없는 수치는 없었다
CHAT_MODEL_NAME = "gpt-6.1-terra"
# 추론 강도. low에서 추론 토큰은 호출당 수백 개 수준이었다(실험 실측)
REASONING_EFFORT = "low"
# 한 호출이 멈춘 채 응답이 없으면 job 전체가 멈춘다. SDK 기본값(10분)은 너무 길어서 줄인다.
# ponytail: SDK가 타임아웃도 2회 재시도하므로 한 호출의 최악은 이 값의 3배. 입력이 큰 호출이
# 이 값을 넘기면 늘린다
LLM_TIMEOUT_SECONDS = 120


def _chat_model(model: str) -> BaseChatModel:
    return ChatOpenAI(
        model=model,
        api_key=settings.OPENAI_API_KEY,
        # 추론 모델은 temperature를 고정할 수 없어서 넘기지 않는다
        reasoning_effort=REASONING_EFFORT,
        timeout=LLM_TIMEOUT_SECONDS,
        callbacks=[LlmUsageCallback()],
    )


@lru_cache
def get_llm() -> BaseChatModel:
    # 문서 파이프라인(extract, draft_card, verify, project_meta)용
    return _chat_model(MODEL_NAME)


@lru_cache
def get_chat_llm() -> BaseChatModel:
    # 챗봇(chat 도메인)용
    return _chat_model(CHAT_MODEL_NAME)


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
