from app.core.text import strip_nul


def test_strip_nul_cleans_nested_strings_and_keeps_other_values():
    data = {"a": "x\x00y", "b": ["k\x00", 1.5, None], "c": {"d": "\x00"}, "e": 3}
    assert strip_nul(data) == {"a": "xy", "b": ["k", 1.5, None], "c": {"d": ""}, "e": 3}
