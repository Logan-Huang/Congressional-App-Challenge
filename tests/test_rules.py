"""Tests for the shared rules every layer relies on."""

from datetime import date

from app.rules import counts_as_health_based, is_current
from app.translate import translate_system


def test_known_counts_as_current_only_when_recent():
    today = date(2026, 10, 7)
    assert is_current({"status": "open"}, today)
    assert is_current({"status": "known", "begin_date": "2023-01-01"}, today)
    assert not is_current({"status": "known", "begin_date": "2019-01-01"}, today)
    assert not is_current({"status": "resolved", "begin_date": "2026-01-01"}, today)


def test_lead_pipe_inventory_is_record_keeping():
    # EPA flags it health-based; we treat it as record-keeping everywhere.
    assert not counts_as_health_based({"contaminant_code": "5200", "is_health_based": True})
    assert counts_as_health_based({"contaminant_code": "1040", "is_health_based": True})
    assert not counts_as_health_based({"contaminant_code": "1040", "is_health_based": False})


def test_inventory_only_system_is_yellow_and_explains_inventory():
    system = {
        "pwsid": "CA0000001", "name": "SMALL FARM", "violations": [{
            "violation_id": "1", "contaminant_code": "5200", "violation_code": "2E",
            "category_code": "TT", "rule_code": "351", "is_health_based": True,
            "status": "open", "begin_date": "2024-10-17", "notification_tier": 2,
        }],
        "history": {"by_year": [], "lead_90th": []},
    }
    result = translate_system(system)
    assert result["status"] == "yellow"
    assert any("does not mean lead was found" in s for s in result["sentences"])
    assert result["guidance"][0]["title"] == "Check your service line for lead"
