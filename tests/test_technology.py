import pytest

from app.core.technology import (
    display_name,
    find_technologies,
    is_valid_id,
    normalize_technologies,
    normalize_technology,
)
from app.core.technology import normalizer as N
from app.core.technology.aliases import AMBIGUOUS_IN_TEXT, TECHNOLOGIES


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Next.js", "nextjs"),
        ("NextJS", "nextjs"),
        ("next js", "nextjs"),
        ("NEXT.JS", "nextjs"),
        ("넥스트", "nextjs"),
        ("넥스트 JS", "nextjs"),
        ("React", "react"),
        ("ReactJS", "react"),
        ("리액트", "react"),
        ("Fast API", "fastapi"),
        ("fastapi", "fastapi"),
        ("PostgreSQL", "postgresql"),
        ("Postgres", "postgresql"),
        ("포스트그레스", "postgresql"),
        ("Node.js", "nodejs"),
        ("Node", "nodejs"),
        ("  Spring  Boot ", "springboot"),
        ("스프링 부트", "springboot"),
    ],
)
def test_aliases_map_to_one_standard_id(name, expected):
    assert normalize_technology(name) == expected


def test_symbols_that_distinguish_technologies_are_kept():
    # C / C++ / C#가 하나로 합쳐지면 안 되고, .NET도 net 같은 일반 이름으로 남으면 안 된다
    ids = {normalize_technology(n) for n in ("C", "C++", "C#")}
    assert ids == {"c", "cpp", "csharp"}
    assert normalize_technology(".NET") == "dotnet"


def test_unknown_technologies_still_match_by_spelling():
    # 사전에 없어도 저장할 때와 검색할 때 같은 철자면 같은 값이 나와야 한다
    assert (
        normalize_technology("SomeNewTool") == normalize_technology("somenewtool") == "somenewtool"
    )
    assert normalize_technology("Some New-Tool") == "somenewtool"


def test_empty_names_are_dropped():
    assert normalize_technology("") is None and normalize_technology(" - . ") is None
    assert normalize_technologies(["", "  "]) == []


def test_normalize_technologies_dedupes_by_standard_id_keeping_order(caplog):
    with caplog.at_level("INFO"):
        result = normalize_technologies(["Next.JS", "넥스트", "FastAPI", "fast api", "Brand New"])
    assert result == ["nextjs", "fastapi", "brandnew"]
    assert "사전에 없는 기술 'Brand New'" in caplog.text and "Next.JS" not in caplog.text


def test_display_name_uses_official_spelling_and_keeps_unknown_as_is():
    assert display_name("nextjs") == "Next.js" and display_name("Supabase") == "Supabase"
    assert display_name("postgres") == "PostgreSQL" and display_name("unknowntool") == "unknowntool"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("넥스트에서 발생한 장애 사례", ["nextjs"]),
        ("Spring Boot와 Redis를 같이 썼다", ["springboot", "redis"]),
        ("리액트랑 Next.js 비교", ["react", "nextjs"]),
        ("Ruby on Rails 마이그레이션", ["rails"]),
        ("langgraph로 파이프라인을 만들었다.", ["langgraph"]),
    ],
)
def test_find_technologies_in_text(text, expected):
    assert find_technologies(text) == expected


def test_ambiguous_names_are_not_picked_from_running_text():
    assert find_technologies("go to the node 5개 노드 뷰") == []
    # 하지만 이름 하나를 정규화할 때는 쓴다
    assert normalize_technology("Go") == "go" and normalize_technology("node") == "nodejs"


def test_valid_id_guards_search_conditions():
    assert is_valid_id("nextjs") and is_valid_id("c++") and is_valid_id("넥스트")
    assert not is_valid_id('a,"b"') and not is_valid_id("a b") and not is_valid_id("")


# --- 사전 일관성: 항목을 추가하다 실수하면 여기서 잡는다 ---


def test_every_standard_id_is_already_normalized_and_has_a_display_name():
    for tech_id, (display, _aliases) in TECHNOLOGIES.items():
        assert N._key(tech_id) == tech_id, f"표준 ID가 정규화된 형태가 아님: {tech_id}"
        assert display.strip(), f"표시명 없음: {tech_id}"
        assert is_valid_id(tech_id)


def test_aliases_resolve_back_to_their_own_standard_id():
    for tech_id, (display, aliases) in TECHNOLOGIES.items():
        for alias in (tech_id, display, *aliases):
            assert normalize_technology(alias) == tech_id, f"{alias!r} -> {tech_id}"


def test_conflicting_aliases_are_rejected_at_build_time(monkeypatch):
    monkeypatch.setattr(N, "TECHNOLOGIES", {"a": ("A", ("same",)), "b": ("B", ("Same",))})
    with pytest.raises(ValueError, match="별칭 충돌"):
        N._build_alias_map()


def test_ambiguous_keys_are_real_aliases():
    for key in AMBIGUOUS_IN_TEXT:
        assert key in N._ALIAS_TO_ID, f"AMBIGUOUS_IN_TEXT에 있지만 사전에 없는 키: {key}"


def test_dictionary_has_around_a_hundred_entries():
    assert 90 <= len(TECHNOLOGIES) <= 150
