import json
from datetime import date
from pathlib import Path

from assurance_adapter import build_position_assurance, write_position_assurance

from sapsf_shared.assurance import validate_assurance_document


def _issue(position="POS-001", severity="CRITICAL"):
    return {
        "Position ID": position,
        "Check ID": "CHK-02",
        "Check Category": "Hierarchy Alignment",
        "Failed Field": "parentPosition",
        "Issue Description": "Parent position is missing.",
        "Severity": severity,
        "Employee ID": "must-not-leak",
    }


def test_groups_positions_without_embedding_position_or_employee_ids(tmp_path: Path):
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"synthetic")
    document = build_position_assurance(
        [_issue("POS-001"), _issue("POS-002")],
        10,
        "GBR",
        date(2026, 8, 12),
        [str(report)],
        run_id="RUN-1",
        tool_version="1.0.0",
    )
    validate_assurance_document(document)
    text = json.dumps(document)
    assert "POS-001" not in text
    assert "POS-002" not in text
    assert "must-not-leak" not in text
    assert document["summary"]["status"] == "blocked"
    assert document["findings"][0]["description"].startswith("2 position(s) affected")


def test_high_only_run_requires_attention(tmp_path: Path):
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"synthetic")
    document = build_position_assurance(
        [_issue(severity="HIGH")],
        1,
        "GBR",
        None,
        [str(report)],
        run_id="RUN-2",
        tool_version="1.0.0",
    )
    assert document["summary"]["status"] == "attention_required"


def test_writer_restricts_assurance_file(tmp_path: Path):
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"synthetic")
    path = Path(write_position_assurance([], 0, "GBR", None, [str(report)], str(tmp_path), "1.0.0"))
    assert path.stat().st_mode & 0o777 == 0o600
    validate_assurance_document(json.loads(path.read_text(encoding="utf-8")))
