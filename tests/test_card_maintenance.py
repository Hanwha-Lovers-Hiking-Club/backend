from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.domains.knowledge_cards import maintenance


def test_inspection_reports_candidates_without_changing_cards(monkeypatch, caplog):
    now = datetime.now(timezone.utc)
    base = {field: "same" for field in maintenance.CONTENT_FIELDS}
    rows = [
        {**base, "id": "first", "created_at": (now - timedelta(days=181)).isoformat(),
         "last_verified_at": None, "content_embedding": "[1,0]"},
        {**base, "id": "second", "created_at": now.isoformat(), "last_verified_at": None,
         "content_embedding": [1, 0]},
        {**base, "id": "different", "title": "different", "created_at": now.isoformat(),
         "last_verified_at": None, "content_embedding": [0.9, 0.1]},
    ]
    for row in rows:
        row["last_viewed_at"] = now.isoformat()
    client = MagicMock()
    query = client.table.return_value.select.return_value.eq.return_value.order.return_value
    query.range.return_value.execute.return_value = SimpleNamespace(data=rows)
    client.rpc.return_value.execute.return_value = SimpleNamespace(
        data=[{"id": "first"}, {"id": "second"}, {"id": "different"}]
    )
    monkeypatch.setattr(maintenance, "supabase", client)
    save = MagicMock()
    monkeypatch.setattr(maintenance, "_save_finding", save)
    judge = MagicMock(return_value=maintenance.DuplicateJudgement(
        verdict="duplicate", reason="같은 사건"
    ))
    monkeypatch.setattr(maintenance, "_judge_pair", judge)

    with caplog.at_level("INFO"):
        maintenance.inspect_cards()

    save.assert_any_call("duplicate", "first", "second", source="exact", verdict="duplicate",
                         reason="같은 사건")
    save.assert_any_call("stale", "first", "first", checked_at=rows[0]["created_at"])
    assert "중복 후보 쌍 3개, 재검토 후보 1개, 미사용 후보 0개, 이력 없음 0개" in caplog.text
    assert judge.call_count == 3
    assert client.rpc.call_count == 3
    client.table.return_value.update.assert_not_called()
    client.table.return_value.delete.assert_not_called()


def test_failed_llm_does_not_report_merge_and_other_pairs_continue(monkeypatch, caplog):
    now = datetime.now(timezone.utc).isoformat()
    base = {field: "same" for field in maintenance.CONTENT_FIELDS}
    rows = [{**base, "id": value, "created_at": now, "last_verified_at": now,
             "last_viewed_at": now}
            for value in ("a", "b", "c")]
    client = MagicMock()
    query = client.table.return_value.select.return_value.eq.return_value.order.return_value
    query.range.return_value.execute.return_value = SimpleNamespace(data=rows)
    monkeypatch.setattr(maintenance, "supabase", client)
    save = MagicMock()
    monkeypatch.setattr(maintenance, "_save_finding", save)
    judge = MagicMock(side_effect=[RuntimeError("failed"), maintenance.DuplicateJudgement(
        verdict="uncertain", reason="정보 부족"
    )])
    monkeypatch.setattr(maintenance, "_judge_pair", judge)

    with caplog.at_level("INFO"):
        maintenance.inspect_cards()

    assert "카드 중복 검증 실패: card_a=a card_b=b" in caplog.text
    save.assert_called_once_with("duplicate", "a", "c", source="exact", verdict="uncertain",
                                 reason="정보 부족")


def test_save_finding_calls_record_rpc(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(maintenance, "supabase", client)
    maintenance._save_finding("duplicate", "a", "b", source="similar", verdict="different",
                              reason="다른 사건")
    client.rpc.assert_called_once_with("record_card_maintenance_finding", {
        "p_kind": "duplicate", "p_card_id": "a", "p_related_card_id": "b",
        "p_source": "similar", "p_verdict": "different", "p_reason": "다른 사건",
        "p_checked_at": None,
    })
    client.rpc.return_value.execute.assert_called_once()


def test_usage_candidates_use_latest_event_and_distinguish_missing_history(monkeypatch):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=181)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    base = {field: "same" for field in maintenance.CONTENT_FIELDS}
    rows = [
        {**base, "id": "old", "title": "old", "created_at": recent,
         "last_viewed_at": old, "last_referenced_at": None},
        {**base, "id": "recent", "title": "recent", "created_at": recent,
         "last_viewed_at": old, "last_referenced_at": recent},
        {**base, "id": "unknown", "title": "unknown", "created_at": old},
    ]
    client = MagicMock()
    query = client.table.return_value.select.return_value.eq.return_value.order.return_value
    query.range.return_value.execute.return_value = SimpleNamespace(data=rows)
    monkeypatch.setattr(maintenance, "supabase", client)
    save = MagicMock()
    monkeypatch.setattr(maintenance, "_save_finding", save)

    maintenance.inspect_cards()

    save.assert_any_call("unused", "old", "old", checked_at=old)
    save.assert_any_call("no_usage", "unknown", "unknown", checked_at=None)
    assert not any(call.args[1] == "recent" for call in save.call_args_list)
