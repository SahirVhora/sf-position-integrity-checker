"""
test_remediation.py - Offline tests for auto-remediation payload generation.

No OData calls. All tests use in-memory lookups and position fixtures.
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from remediation import _build_payload, _date_to_epoch_ms, build_remediation_pack

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_lookups() -> dict:
    return {
        "companies": {
            "LE-1": {
                "externalCode": "LE-1",
                "startDate": "2020-01-01",
                "endDate": "9999-12-31",
                "status": "A",
            },
        },
        "business_units": {
            "BU-1": {
                "externalCode": "BU-1",
                "startDate": "2020-01-01",
                "endDate": "9999-12-31",
                "status": "A",
            },
        },
        "divisions": {
            "DIV-1": {
                "externalCode": "DIV-1",
                "startDate": "2020-01-01",
                "endDate": "9999-12-31",
                "status": "A",
            },
        },
        "departments": {
            "DEPT-1": {
                "externalCode": "DEPT-1",
                "startDate": "2020-01-01",
                "endDate": "9999-12-31",
                "status": "A",
                "cust_Division": "DIV-1",
            },
        },
        "sub_departments": {
            "SUBDEPT-1": {
                "externalCode": "SUBDEPT-1",
                "startDate": "2020-01-01",
                "endDate": "9999-12-31",
                "status": "A",
                "cust_Department": "DEPT-1",
            },
        },
        "job_codes": {
            "JC-1": {
                "externalCode": "JC-1",
                "startDate": "2020-01-01",
                "endDate": "9999-12-31",
                "status": "A",
                "jobFunction": "JF-TECH",
                "cust_jobsubfunction": "JSF-ENG",
                "grade": "G5",
                "cust_careerPath": "CP-TECH",
            },
        },
        "cost_centers": {
            "CC-1": {
                "externalCode": "CC-1",
                "startDate": "2020-01-01",
                "endDate": "9999-12-31",
                "status": "A",
            },
        },
        "locations": {
            "LOC-1": {
                "externalCode": "LOC-1",
                "startDate": "2020-01-01",
                "endDate": "9999-12-31",
                "status": "A",
            },
        },
        "empjob": {},
        "div_to_bus": {"DIV-1": {"BU-1"}},
        "bu_to_les": {"BU-1": {"LE-1"}},
        "cc_to_bus": {"CC-1": {"BU-1"}},
    }


def _make_position(overrides: dict | None = None) -> dict:
    pos = {
        "code": "POS-1",
        "externalName_en_US": "Test Position",
        "effectiveStartDate": "2022-01-01",
        "effectiveEndDate": "9999-12-31",
        "effectiveStatus": "A",
        "company": "LE-1",
        "businessUnit": "BU-1",
        "division": "DIV-1",
        "department": "DEPT-1",
        "cust_subDepartment": "SUBDEPT-1",
        "jobCode": "JC-1",
        "costCenter": "CC-1",
        "location": "LOC-1",
        "cust_Country": "GB",
        "cust_JobFunction": "JF-TECH",
        "cust_jobSubFunction": "JSF-ENG",
        "cust_GlobalJobLevel": "G5",
        "cust_CareerPath": "CP-TECH",
        "payGrade": None,
    }
    if overrides:
        pos.update(overrides)
    return pos


def _chk_issue(check_id: str, pos_code: str = "POS-1", title: str = "Test Position") -> dict:
    return {
        "Position ID": pos_code,
        "Position Title": title,
        "Check ID": check_id,
        "Issue Description": f"Mismatch for {check_id}",
        "Severity": "CRITICAL",
        "Failed Field": "someField",
    }


# ---------------------------------------------------------------------------
# Tests: payload correctness per check
# ---------------------------------------------------------------------------


def test_chk02_payload_sets_correct_division():
    """CHK-02: position.division should be updated to DEPT-1's cust_Division."""
    pos = _make_position({"division": "DIV-WRONG"})
    lookups = _make_lookups()
    issues = [_chk_issue("CHK-02")]

    entries = build_remediation_pack(issues, lookups, [pos])

    assert len(entries) == 1
    e = entries[0]
    assert not e.skipped
    assert e.position_field == "division"
    assert e.new_value == "DIV-1"
    assert e.confidence == "HIGH"
    assert e.payload["division"] == "DIV-1"
    assert e.payload["code"] == "POS-1"


def test_chk06_payload_sets_correct_job_function():
    """CHK-06: position.cust_JobFunction updated to JC-1's jobFunction."""
    pos = _make_position({"cust_JobFunction": "JF-WRONG"})
    lookups = _make_lookups()
    issues = [_chk_issue("CHK-06")]

    entries = build_remediation_pack(issues, lookups, [pos])

    assert len(entries) == 1
    e = entries[0]
    assert not e.skipped
    assert e.new_value == "JF-TECH"
    assert e.payload["cust_JobFunction"] == "JF-TECH"


def test_chk07_payload_sets_correct_sub_function():
    pos = _make_position({"cust_jobSubFunction": "JSF-WRONG"})
    issues = [_chk_issue("CHK-07")]
    entries = build_remediation_pack(issues, _make_lookups(), [pos])

    e = entries[0]
    assert not e.skipped
    assert e.new_value == "JSF-ENG"
    assert e.payload["cust_jobSubFunction"] == "JSF-ENG"


def test_chk08_payload_sets_correct_grade():
    pos = _make_position({"cust_GlobalJobLevel": "G9"})
    issues = [_chk_issue("CHK-08")]
    entries = build_remediation_pack(issues, _make_lookups(), [pos])

    e = entries[0]
    assert not e.skipped
    assert e.new_value == "G5"
    assert e.payload["cust_GlobalJobLevel"] == "G5"


def test_chk09_payload_sets_correct_career_path():
    pos = _make_position({"cust_CareerPath": "CP-WRONG"})
    issues = [_chk_issue("CHK-09")]
    entries = build_remediation_pack(issues, _make_lookups(), [pos])

    e = entries[0]
    assert not e.skipped
    assert e.new_value == "CP-TECH"


def test_chk03_junction_single_bu_is_high_confidence():
    """CHK-03: single valid BU in junction = HIGH confidence."""
    pos = _make_position({"businessUnit": "BU-WRONG"})
    issues = [_chk_issue("CHK-03")]
    entries = build_remediation_pack(issues, _make_lookups(), [pos])

    e = entries[0]
    assert not e.skipped
    assert e.new_value == "BU-1"
    assert e.confidence == "HIGH"
    assert e.payload["businessUnit"] == "BU-1"


def test_chk03_junction_multiple_bus_is_medium_confidence():
    """CHK-03: multiple valid BUs in junction = MEDIUM confidence."""
    lookups = _make_lookups()
    lookups["div_to_bus"]["DIV-1"] = {"BU-1", "BU-2"}

    pos = _make_position({"businessUnit": "BU-WRONG"})
    issues = [_chk_issue("CHK-03")]
    entries = build_remediation_pack(issues, lookups, [pos])

    e = entries[0]
    assert not e.skipped
    assert e.confidence == "MEDIUM"
    assert e.new_value in ("BU-1", "BU-2")  # sorted, first selected


def test_chk01_payload_sets_correct_department():
    """CHK-01: position.department updated to SUBDEPT-1's cust_Department."""
    pos = _make_position({"department": "DEPT-WRONG"})
    issues = [_chk_issue("CHK-01")]
    entries = build_remediation_pack(issues, _make_lookups(), [pos])

    e = entries[0]
    assert not e.skipped
    assert e.new_value == "DEPT-1"
    assert e.payload["department"] == "DEPT-1"


def test_foundation_active_checks_skipped():
    """CHK-10 to CHK-17 (foundation_active) produce no remediation entries."""
    pos = _make_position()
    issues = [_chk_issue(f"CHK-{i:02d}") for i in range(10, 18)]
    entries = build_remediation_pack(issues, _make_lookups(), [pos])

    assert len(entries) == 0, "Foundation-active checks should produce no remediation entries"


def test_skipped_when_position_missing_from_extract():
    """Issue with position code not in positions list → skipped entry."""
    lookups = _make_lookups()
    issues = [_chk_issue("CHK-02", pos_code="POS-GHOST")]
    entries = build_remediation_pack(issues, lookups, [])  # empty positions list

    assert len(entries) == 1
    e = entries[0]
    assert e.skipped
    assert "not found" in e.skip_reason.lower()


def test_skipped_when_foundation_record_blank_value():
    """Skip when foundation record has blank grade (CHK-08 with no grade)."""
    lookups = _make_lookups()
    lookups["job_codes"]["JC-1"] = dict(lookups["job_codes"]["JC-1"])
    lookups["job_codes"]["JC-1"]["grade"] = ""

    pos = _make_position({"cust_GlobalJobLevel": "G9"})
    issues = [_chk_issue("CHK-08")]
    entries = build_remediation_pack(issues, lookups, [pos])

    e = entries[0]
    assert e.skipped
    assert "blank" in e.skip_reason.lower()


def test_payload_includes_effective_start_date():
    """Payload must include effectiveStartDate as /Date(ms)/."""
    pos = _make_position({"effectiveStartDate": "2022-01-01"})
    payload = _build_payload(pos, "division", "DIV-1")

    assert "effectiveStartDate" in payload
    assert payload["effectiveStartDate"].startswith("/Date(")
    assert payload["code"] == "POS-1"


def test_date_to_epoch_ms_known_date():
    """2022-01-01 = 1640995200000 ms."""
    result = _date_to_epoch_ms("2022-01-01")
    assert result == 1640995200000


def test_date_to_epoch_ms_invalid_returns_none():
    assert _date_to_epoch_ms("not-a-date") is None
    assert _date_to_epoch_ms("") is None


def test_dry_run_writes_json_output(tmp_path, monkeypatch):
    """apply_remediation dry_run=True writes JSON file to output/."""

    monkeypatch.chdir(tmp_path)
    os.makedirs("output", exist_ok=True)

    pos = _make_position({"division": "DIV-WRONG"})
    lookups = _make_lookups()
    issues = [_chk_issue("CHK-02")]
    entries = build_remediation_pack(issues, lookups, [pos])

    from remediation import apply_remediation

    result = apply_remediation(entries, country="TEST", dry_run=True)

    assert result.dry_run is True
    assert result.applied == 0

    # JSON file must exist and be valid
    today = datetime.date.today().strftime("%Y%m%d")
    json_file = tmp_path / "output" / f"remediation_pack_TEST_{today}.json"
    assert json_file.exists(), "JSON pack must be written"

    with open(json_file) as f:
        doc = json.load(f)

    assert doc["schema"] == "sf-remediation-pack/v1"
    assert doc["dry_run"] is True
    assert doc["total"] == 1
    assert len(doc["entries"]) == 1


def test_dry_run_writes_excel_output(tmp_path, monkeypatch):
    """apply_remediation dry_run=True writes .xlsx file."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("output", exist_ok=True)

    pos = _make_position({"cust_GlobalJobLevel": "G9"})
    issues = [_chk_issue("CHK-08")]
    entries = build_remediation_pack(issues, _make_lookups(), [pos])

    from remediation import apply_remediation

    apply_remediation(entries, country="TEST", dry_run=True)

    today = datetime.date.today().strftime("%Y%m%d")
    xlsx_file = tmp_path / "output" / f"remediation_pack_TEST_{today}.xlsx"
    assert xlsx_file.exists(), "Excel pack must be written"


def test_mixed_issues_correct_counts(tmp_path, monkeypatch):
    """Multiple issues across check types: counts are correct."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("output", exist_ok=True)

    pos1 = _make_position({"code": "P1", "division": "DIV-WRONG"})
    pos2 = _make_position({"code": "P2", "cust_GlobalJobLevel": "G9"})
    pos3 = _make_position({"code": "P3"})  # CHK-10 issue - should be skipped

    issues = [
        _chk_issue("CHK-02", "P1"),
        _chk_issue("CHK-08", "P2"),
        _chk_issue("CHK-10", "P3"),  # foundation_active - no remediation
    ]

    entries = build_remediation_pack(issues, _make_lookups(), [pos1, pos2, pos3])

    assert len(entries) == 2
    actionable = [e for e in entries if not e.skipped]
    assert len(actionable) == 2
    check_ids = {e.check_id for e in actionable}
    assert check_ids == {"CHK-02", "CHK-08"}
