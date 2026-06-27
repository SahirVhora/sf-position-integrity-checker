"""
test_simulator.py - Offline tests for Mode 2 pre-change simulation.

Uses isolated SQLite fixtures per test (no OData calls, no real tenant).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import database as db
from simulator import _apply_change, simulate_change

# ---------------------------------------------------------------------------
# Helpers to build a minimal test DB
# ---------------------------------------------------------------------------


def _make_db(tmp_path) -> str:
    """Create an isolated SQLite DB with a minimal position + FO dataset."""
    db_file = str(tmp_path / "test.db")
    orig = db.DB_PATH
    db.DB_PATH = db_file
    try:
        db.init_db()
        _seed(db_file)
    finally:
        db.DB_PATH = orig
    return db_file


def _seed(db_file: str) -> None:
    """Populate the DB with two positions and matching foundation objects."""
    import sqlite3

    conn = sqlite3.connect(db_file)

    # Foundation hierarchy: LE-1 <- BU-1 <- DIV-1 <- DEPT-1 <- SUBDEPT-1
    conn.execute(
        "INSERT INTO fo_company VALUES (?,?,?,?,?,?)",
        ("LE-1", "2020-01-01", "9999-12-31", "A", "Legal Entity 1", "GB"),
    )
    conn.execute(
        "INSERT INTO fo_business_unit VALUES (?,?,?,?,?)",
        ("BU-1", "2020-01-01", "9999-12-31", "A", "Business Unit 1"),
    )
    conn.execute(
        "INSERT INTO fo_division VALUES (?,?,?,?,?)",
        ("DIV-1", "2020-01-01", "9999-12-31", "A", "Division 1"),
    )
    conn.execute(
        "INSERT INTO fo_department VALUES (?,?,?,?,?,?)",
        ("DEPT-1", "2020-01-01", "9999-12-31", "A", "Department 1", "DIV-1"),
    )
    conn.execute(
        "INSERT INTO cust_sub_department VALUES (?,?,?,?,?,?)",
        ("SUBDEPT-1", "2020-01-01", "9999-12-31", "A", "Sub Dept 1", "DEPT-1"),
    )

    # Job code
    conn.execute(
        "INSERT INTO fo_job_code VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "JC-1",
            "2020-01-01",
            "9999-12-31",
            "A",
            "Job Code 1",
            "JF-TECH",
            "JSF-ENG",
            "G5",
            "CP-TECH",
        ),
    )

    # Junction tables
    conn.execute("INSERT INTO fo_division_business_unit VALUES (?,?)", ("DIV-1", "BU-1"))
    conn.execute("INSERT INTO fo_bu_legal_entity VALUES (?,?)", ("BU-1", "LE-1"))
    conn.execute("INSERT INTO fo_cost_center_business_unit VALUES (?,?)", ("CC-1", "BU-1"))

    # Cost centre
    conn.execute(
        "INSERT INTO fo_cost_center VALUES (?,?,?,?,?)",
        ("CC-1", "2020-01-01", "9999-12-31", "A", "Cost Centre 1"),
    )

    # Location
    conn.execute(
        "INSERT INTO fo_location VALUES (?,?,?,?,?)",
        ("LOC-1", "2020-01-01", "9999-12-31", "A", "London"),
    )

    # Positions - POS-1 is clean, POS-2 has a pre-existing CHK-02 failure
    conn.execute(
        """INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "POS-1",
            "Clean Position",
            "2022-01-01",
            "9999-12-31",
            "A",
            "LE-1",
            "BU-1",
            "DIV-1",
            "DEPT-1",
            "SUBDEPT-1",
            "JC-1",
            "CC-1",
            "LOC-1",
            "GB",
            "JF-TECH",
            "JSF-ENG",
            "G5",
            "CP-TECH",
            None,  # payGrade
        ),
    )
    conn.execute(
        """INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "POS-2",
            "Already Broken",
            "2022-01-01",
            "9999-12-31",
            "A",
            "LE-1",
            "BU-1",
            "DIV-WRONG",  # wrong division - pre-existing CHK-02 fail
            "DEPT-1",
            "SUBDEPT-1",
            "JC-1",
            "CC-1",
            "LOC-1",
            "GB",
            "JF-TECH",
            "JSF-ENG",
            "G5",
            "CP-TECH",
            None,
        ),
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reparent_department_causes_new_failures(tmp_path):
    """Moving DEPT-1 to DIV-2 breaks POS-1 (CHK-02)."""
    db_file = _make_db(tmp_path)
    result = simulate_change(
        change={
            "type": "reparent",
            "entity_type": "departments",
            "code": "DEPT-1",
            "field": "cust_Division",
            "old_value": "DIV-1",
            "new_value": "DIV-2",
        },
        db_path=db_file,
    )

    newly_failing_ids = {i["Position ID"] for i in result["newly_failing"]}
    newly_failing_checks = {i["Check ID"] for i in result["newly_failing"]}

    assert "POS-1" in newly_failing_ids, "Clean position should now fail CHK-02"
    assert "CHK-02" in newly_failing_checks
    assert result["net_impact"] > 0, "Net impact should be positive (more failures)"


def test_reparent_department_can_fix_existing_failures(tmp_path):
    """
    POS-2 has division=DIV-WRONG but DEPT-1.cust_Division=DIV-1 (mismatch -> CHK-02 fail).
    If we reparent DEPT-1 to DIV-WRONG, POS-2 should newly pass.
    """
    db_file = _make_db(tmp_path)
    result = simulate_change(
        change={
            "type": "reparent",
            "entity_type": "departments",
            "code": "DEPT-1",
            "field": "cust_Division",
            "old_value": "DIV-1",
            "new_value": "DIV-WRONG",
        },
        db_path=db_file,
    )

    newly_passing_ids = {i["Position ID"] for i in result["newly_passing"]}
    assert "POS-2" in newly_passing_ids, "POS-2 CHK-02 failure should be resolved"


def test_field_change_job_code_grade_breaks_matching_positions(tmp_path):
    """Changing JC-1.grade from G5 to G6 breaks POS-1 (CHK-08) which has G5."""
    db_file = _make_db(tmp_path)
    result = simulate_change(
        change={
            "type": "field_change",
            "entity_type": "job_codes",
            "code": "JC-1",
            "field": "grade",
            "old_value": "G5",
            "new_value": "G6",
        },
        db_path=db_file,
    )

    newly_failing_checks = {i["Check ID"] for i in result["newly_failing"]}
    newly_failing_ids = {i["Position ID"] for i in result["newly_failing"]}

    assert "CHK-08" in newly_failing_checks
    assert "POS-1" in newly_failing_ids


def test_change_unreferenced_entity_has_zero_impact(tmp_path):
    """Changing a department not referenced by any position has no impact."""
    db_file = _make_db(tmp_path)

    # Insert an extra department not linked to any position
    import sqlite3

    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO fo_department VALUES (?,?,?,?,?,?)",
        ("DEPT-ORPHAN", "2020-01-01", "9999-12-31", "A", "Orphan Dept", "DIV-1"),
    )
    conn.commit()
    conn.close()

    result = simulate_change(
        change={
            "type": "reparent",
            "entity_type": "departments",
            "code": "DEPT-ORPHAN",
            "field": "cust_Division",
            "new_value": "DIV-999",
        },
        db_path=db_file,
    )

    assert len(result["newly_failing"]) == 0
    assert len(result["newly_passing"]) == 0
    assert result["net_impact"] == 0


def test_reparent_division_junction_update(tmp_path):
    """
    Moving DIV-1 to BU-2 (junction update) should break POS-1 (CHK-03)
    since POS-1.businessUnit=BU-1 but DIV-1 would only be linked to BU-2.
    """
    db_file = _make_db(tmp_path)
    result = simulate_change(
        change={
            "type": "reparent",
            "entity_type": "divisions",
            "code": "DIV-1",
            "field": "businessUnit",
            "old_value": "BU-1",
            "new_value": "BU-2",
        },
        db_path=db_file,
    )

    newly_failing_checks = {i["Check ID"] for i in result["newly_failing"]}
    assert "CHK-03" in newly_failing_checks


def test_result_structure(tmp_path):
    """simulate_change always returns required keys."""
    db_file = _make_db(tmp_path)
    result = simulate_change(
        change={
            "type": "field_change",
            "entity_type": "job_codes",
            "code": "JC-1",
            "field": "jobFunction",
            "new_value": "JF-OTHER",
        },
        db_path=db_file,
    )

    for key in (
        "proposed_change",
        "total_positions",
        "newly_failing",
        "newly_passing",
        "unchanged_failing",
        "net_impact",
        "check_breakdown",
        "as_of_date",
    ):
        assert key in result, f"Missing key: {key}"
    assert result["total_positions"] == 2


def test_invalid_entity_type_raises(tmp_path):
    db_file = _make_db(tmp_path)
    with pytest.raises(ValueError, match="Unsupported entity_type"):
        simulate_change(
            change={
                "type": "reparent",
                "entity_type": "nonexistent_table",
                "code": "X",
                "field": "y",
                "new_value": "z",
            },
            db_path=db_file,
        )


def test_invalid_change_type_raises(tmp_path):
    db_file = _make_db(tmp_path)
    with pytest.raises(ValueError, match="change type must be"):
        simulate_change(
            change={
                "type": "delete",
                "entity_type": "departments",
                "code": "DEPT-1",
                "field": "cust_Division",
                "new_value": "DIV-2",
            },
            db_path=db_file,
        )


def test_apply_change_missing_code_is_noop():
    """_apply_change with a code not in lookups does not raise."""
    lookups = {"departments": {}}
    _apply_change(
        lookups,
        {
            "type": "reparent",
            "entity_type": "departments",
            "code": "NONEXISTENT",
            "field": "cust_Division",
            "new_value": "DIV-X",
        },
    )
    # Should not raise; no-op
    assert lookups["departments"] == {}
