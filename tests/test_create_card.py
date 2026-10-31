import pytest

from app.domains.knowledge_cards import service as K


class _FakeSupabase:
    def __init__(self, fail_evidence: bool, fail_delete: bool = False):
        self.fail_evidence, self.fail_delete = fail_evidence, fail_delete
        self.calls: list[tuple[str, str]] = []
        self._table = ""

    def table(self, name):
        self._table = name
        return self

    def insert(self, rows):
        self._op = "insert"
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        self.calls.append((self._table, self._op))
        if self._table == "card_documents" and self.fail_evidence:
            raise RuntimeError("fk violation")
        if self._op == "delete" and self.fail_delete:
            raise RuntimeError("delete failed")
        return type("R", (), {"data": [{"id": "card-1"}]})()


def test_card_is_removed_when_evidence_insert_fails(monkeypatch):
    fake = _FakeSupabase(fail_evidence=True)
    monkeypatch.setattr(K, "supabase", fake)
    with pytest.raises(RuntimeError, match="fk violation"):
        K.create_card({"title": "t"}, [{"document_id": "d"}])
    assert fake.calls == [
        ("knowledge_cards", "insert"),
        ("card_documents", "insert"),
        ("knowledge_cards", "delete"),
    ]


def test_original_error_survives_a_failed_cleanup(monkeypatch):
    monkeypatch.setattr(K, "supabase", _FakeSupabase(fail_evidence=True, fail_delete=True))
    with pytest.raises(RuntimeError, match="fk violation"):
        K.create_card({"title": "t"}, [{"document_id": "d"}])


def test_success_path_does_not_delete(monkeypatch):
    fake = _FakeSupabase(fail_evidence=False)
    monkeypatch.setattr(K, "supabase", fake)
    assert K.create_card({"title": "t"}, [{"document_id": "d"}]) == "card-1"
    assert ("knowledge_cards", "delete") not in fake.calls
