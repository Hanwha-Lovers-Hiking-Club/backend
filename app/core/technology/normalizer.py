# 기술명 정규화: 서로 다른 표기(Next.js, NextJS, 넥스트)를 하나의 표준 ID(nextjs)로 통일한다.
# 문서 인제스트(저장), 챗봇 검색, 지식카드 페이지 검색창이 같은 규칙을 쓰도록 이 모듈을 공유한다.
#
# 처리 순서: (1) 문자열 정규화 (2) 별칭 사전 조회.
# 사전에 없는 기술은 (1)까지만 적용한 값을 표준 ID로 쓴다. 그래야 저장할 때와 검색할 때
# 같은 철자끼리는 사전에 없어도 일치한다(원래 철자 그대로 `LangGraph`로 저장하면
# `langgraph`로 검색했을 때 영원히 못 찾는다).
import logging
import re
import unicodedata

from app.core.technology.aliases import AMBIGUOUS_IN_TEXT, TECHNOLOGIES

logger = logging.getLogger(__name__)

# 구분만 위한 문자는 지운다. `+`, `#`은 기술을 구별하는 기호라 남긴다(C, C++, C#)
_SEPARATORS = re.compile(r"[\s\-_.]+")
# 표준 ID로 쓸 수 있는 문자(검색 조건에 안전하게 넣을 수 있는 값)
_ID_CHARS = re.compile(r"[0-9a-z가-힣+#]+")
_TOKEN = re.compile(r"[0-9A-Za-z가-힣+#.]+")
# 한국어 조사. 본문에서 "넥스트에서"처럼 붙어 있는 경우를 떼어내기 위한 것(긴 것부터 확인)
# fmt: off
_JOSA = (
    "에서는", "에서", "으로", "이랑", "에는", "에도", "까지", "부터", "에게",
    "을", "를", "은", "는", "이", "가", "의", "에", "로", "도", "만", "랑", "와", "과",
)
# fmt: on


def _key(name: str) -> str:
    return _SEPARATORS.sub("", unicodedata.normalize("NFKC", name).casefold())


def _build_alias_map() -> tuple[dict[str, str], dict[str, str]]:
    alias_to_id: dict[str, str] = {}
    display: dict[str, str] = {}
    for tech_id, (display_name_, aliases) in TECHNOLOGIES.items():
        display[tech_id] = display_name_
        for alias in (tech_id, display_name_, *aliases):
            key = _key(alias)
            if alias_to_id.setdefault(key, tech_id) != tech_id:
                # 같은 별칭이 두 기술에 걸리면 어느 쪽인지 알 수 없다. 덮어쓰지 않고 바로 알린다
                raise ValueError(f"별칭 충돌: {alias!r}({key!r}) -> {alias_to_id[key]}, {tech_id}")
    return alias_to_id, display


_ALIAS_TO_ID, _DISPLAY = _build_alias_map()
_TEXT_ALIASES = {k: v for k, v in _ALIAS_TO_ID.items() if k not in AMBIGUOUS_IN_TEXT}


def normalize_technology(name: str) -> str | None:
    # 사전에 있으면 표준 ID, 없으면 문자열 정규화만 적용한 값. 비어 있으면 None
    key = _key(name)
    if not key:
        return None
    return _ALIAS_TO_ID.get(key, key)


def normalize_technologies(names: list[str]) -> list[str]:
    # 인제스트에서 쓴다. 표준 ID 기준으로 중복을 제거하고(순서 유지),
    # 사전에 없는 기술은 로그로 남긴다
    result: list[str] = []
    for name in names:
        tech_id = normalize_technology(name)
        if tech_id is None or tech_id in result:
            continue
        if tech_id not in _DISPLAY:
            logger.info("technology: 사전에 없는 기술 %r -> %r", name, tech_id)
        result.append(tech_id)
    return result


def display_name(tech: str) -> str:
    # 화면 표시용 이름. 표준 ID뿐 아니라 정규화 전 값("Supabase")도 공식 표기로 바꾼다.
    # 사전에 없는 기술은 받은 값을 그대로 돌려준다
    tech_id = _ALIAS_TO_ID.get(_key(tech))
    return _DISPLAY[tech_id] if tech_id else tech


def is_valid_id(tech_id: str) -> bool:
    # 검색 조건에 그대로 넣어도 안전한 값인지
    return bool(_ID_CHARS.fullmatch(tech_id))


def _match(key: str) -> str | None:
    if tech_id := _TEXT_ALIASES.get(key):
        return tech_id
    for josa in _JOSA:
        if key.endswith(josa) and len(key) > len(josa):
            if tech_id := _TEXT_ALIASES.get(key[: -len(josa)]):
                return tech_id
    return None


def find_technologies(text: str) -> list[str]:
    # 문장에서 사전에 등록된 기술을 찾는다(LLM 없이). 챗봇 질문 분석 보조, 검색창 입력 해석용.
    # "Spring Boot"처럼 여러 단어 이름이 있어서 단어 3개, 2개, 1개 순으로 본다(긴 것 우선).
    # 일반 단어와 겹치는 짧은 이름(go, node, 노드 등)은 제외한다.
    tokens = [t.strip(".") for t in _TOKEN.findall(unicodedata.normalize("NFKC", text))]
    tokens = [t for t in tokens if t]
    found: list[str] = []
    i = 0
    while i < len(tokens):
        for size in (3, 2, 1):
            if i + size > len(tokens):
                continue
            tech_id = _match(_key("".join(tokens[i : i + size])))
            if tech_id:
                if tech_id not in found:
                    found.append(tech_id)
                i += size
                break
        else:
            i += 1
    return found
