"""
test_schema.py - Offline test suite for the refactored SQLite schema.

Runs without any SF API credentials. Uses synthetic data to verify:
  1. Schema structure  (tables, indexes, views, CHECK constraints)
  2. Junction table population and set-based lookups
  3. All integrity checks CHK-01 to CHK-09 (pass + fail cases)
  4. validation_results save/load (new schema with extract_meta_id FK)
  5. Date normalisation (epoch-millis → YYYY-MM-DD)
  6. cust_sub_department standardised column names

Usage:
    cd sf_position_integrity_checker
    python test_schema.py
"""

import datetime
import os
import sys
import sqlite3

# Ensure imports work from project root
sys.path.insert(0, os.path.dirname(__file__))

import database as db
from validators import validate_positions, build_lookups_from_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def _ok(label: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  [PASS] {label}")


def _fail(label: str, detail: str = "") -> None:
    global _FAIL
    _FAIL += 1
    msg = f"  [FAIL] {label}"
    if detail:
        msg += f"\n         {detail}"
    print(msg)


def assert_eq(label: str, actual, expected) -> None:
    if actual == expected:
        _ok(label)
    else:
        _fail(label, f"expected {expected!r}, got {actual!r}")


def assert_true(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        _ok(label)
    else:
        _fail(label, detail)


def assert_raises(label: str, exc_type, fn) -> None:
    try:
        fn()
        _fail(label, f"expected {exc_type.__name__} but no exception raised")
    except exc_type:
        _ok(label)
    except Exception as e:
        _fail(label, f"expected {exc_type.__name__} but got {type(e).__name__}: {e}")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(db.DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ---------------------------------------------------------------------------
# 1. Schema structure tests
# ---------------------------------------------------------------------------


def test_schema_structure():
    print("\n--- 1. Schema structure ---")
    c = _conn()

    # Tables that must exist
    expected_tables = {
        "extract_meta",
        "positions",
        "fo_company",
        "fo_business_unit",
        "fo_division",
        "fo_department",
        "cust_sub_department",
        "fo_job_code",
        "fo_job_class_local_can",
        "fo_cost_center",
        "fo_location",
        "fo_division_business_unit",
        "fo_bu_legal_entity",
        "fo_cost_center_business_unit",
        "validation_results",
    }
    existing = {
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    for t in expected_tables:
        assert_true(f"table '{t}' exists", t in existing)

    # Views
    expected_views = {"chk01_failures", "chk03_failures", "chk04_failures"}
    existing_views = {
        r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='view'")
    }
    for v in expected_views:
        assert_true(f"view '{v}' exists", v in existing_views)

    # Indexes on validation_results
    idx_sql = c.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='validation_results'"
    ).fetchall()
    idx_names = {r[0] for r in idx_sql}
    for idx in (
        "idx_vr_extract",
        "idx_vr_run_severity",
        "idx_vr_check_id",
        "idx_vr_position",
    ):
        assert_true(f"index '{idx}' exists", idx in idx_names)

    # Pipe-sep columns must NOT exist on main tables
    def col_exists(table, col):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
        return col in cols

    assert_true(
        "fo_business_unit has NO cust_legalEntity column",
        not col_exists("fo_business_unit", "cust_legalEntity"),
    )
    assert_true(
        "fo_division has NO cust_BusinessUnit column",
        not col_exists("fo_division", "cust_BusinessUnit"),
    )
    assert_true(
        "fo_cost_center has NO cust_BusinessUnit column",
        not col_exists("fo_cost_center", "cust_BusinessUnit"),
    )

    # cust_sub_department must use standard column names
    assert_true(
        "cust_sub_department has 'startDate'",
        col_exists("cust_sub_department", "startDate"),
    )
    assert_true(
        "cust_sub_department has 'endDate'",
        col_exists("cust_sub_department", "endDate"),
    )
    assert_true(
        "cust_sub_department has 'status'", col_exists("cust_sub_department", "status")
    )
    assert_true(
        "cust_sub_department has NO 'effectiveStartDate'",
        not col_exists("cust_sub_department", "effectiveStartDate"),
    )
    assert_true(
        "cust_sub_department has NO 'mdfSystemStatus'",
        not col_exists("cust_sub_department", "mdfSystemStatus"),
    )

    # validation_results must NOT have stale snapshot columns
    for removed_col in (
        "company",
        "businessUnit",
        "division",
        "department",
        "cust_subDepartment",
        "jobCode",
        "costCenter",
        "location",
    ):
        assert_true(
            f"validation_results has NO '{removed_col}'",
            not col_exists("validation_results", removed_col),
        )

    # validation_results must have new columns
    assert_true(
        "validation_results has 'extract_meta_id'",
        col_exists("validation_results", "extract_meta_id"),
    )

    c.close()


# ---------------------------------------------------------------------------
# 2. CHECK constraint tests
# ---------------------------------------------------------------------------


def test_check_constraints():
    print("\n--- 2. CHECK constraints ---")

    # extract_complete must be 0 or 1
    c = _conn()
    assert_raises(
        "extract_complete=2 violates CHECK",
        sqlite3.IntegrityError,
        lambda: c.execute(
            "INSERT INTO extract_meta (run_timestamp,country,extract_complete) VALUES (?,?,?)",
            ("2024-01-01T00:00:00", "CAN", 2),
        ),
    )
    c.close()

    # severity must be in allowed values - first insert a valid extract_meta to satisfy FK
    c = _conn()
    c.execute(
        "INSERT INTO extract_meta (run_timestamp,country,positions_fetched,extract_complete) "
        "VALUES ('2024-01-01T00:00:00','CAN',0,1)"
    )
    meta_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit()
    assert_raises(
        "severity='BAD' violates CHECK",
        sqlite3.IntegrityError,
        lambda: c.execute(
            "INSERT INTO validation_results "
            "(extract_meta_id,run_timestamp,position_code,check_id,check_category,"
            "failed_field,issue_description,severity) VALUES (?,?,?,?,?,?,?,?)",
            (
                meta_id,
                "2024-01-01T00:00:00",
                "P001",
                "CHK-01",
                "Hierarchy Alignment",
                "department",
                "desc",
                "INVALID",
            ),
        ),
    )
    c.close()

    # positions code='' violates CHECK(length(code)>0)
    c = _conn()
    assert_raises(
        "positions code='' violates CHECK",
        sqlite3.IntegrityError,
        lambda: c.execute("INSERT INTO positions (code) VALUES ('')"),
    )
    c.close()

    _ok("CHECK constraints enforce data integrity")


# ---------------------------------------------------------------------------
# 3. Date normalisation
# ---------------------------------------------------------------------------


def test_date_normalisation():
    print("\n--- 3. Date normalisation ---")

    cases = [
        ("/Date(1609459200000)/", "2021-01-01"),  # positive epoch
        ("/Date(-2208988800000)/", "1900-01-01"),  # negative epoch (pre-1970)
        ("2024-03-15T00:00:00", "2024-03-15"),  # ISO datetime truncated
        ("2024-03-15", "2024-03-15"),  # already ISO date
        ("", ""),  # empty passthrough
        (None, None),  # None passthrough
    ]
    for raw, expected in cases:
        result = db.normalise_date(raw) if raw is not None else db.normalise_date(raw)
        assert_eq(f"normalise_date({raw!r})", result, expected)

    # Verify dates are normalised when saved via save_foundation (goes through _bulk_insert)
    # 1609459200000 ms = 2021-01-01T00:00:00Z
    # 1735689600000 ms = 2025-01-01T00:00:00Z
    db.save_foundation(
        "fo_location",
        [
            {
                "externalCode": "LOC-DATETEST",
                "startDate": "/Date(1609459200000)/",
                "endDate": "/Date(1735689600000)/",
                "status": "A",
                "description": "Date Normalisation Test",
            }
        ],
    )
    c = _conn()
    row = dict(
        c.execute(
            "SELECT startDate, endDate FROM fo_location WHERE externalCode='LOC-DATETEST'"
        ).fetchone()
    )
    c.close()
    assert_eq(
        "epoch-millis startDate normalised to ISO via save_foundation",
        row["startDate"],
        "2021-01-01",
    )
    assert_eq(
        "epoch-millis endDate normalised to ISO via save_foundation",
        row["endDate"],
        "2025-01-01",
    )


# ---------------------------------------------------------------------------
# 4. Junction table population and set-based lookups
# ---------------------------------------------------------------------------


def _insert_synthetic_data(c: sqlite3.Connection) -> None:
    """Insert a minimal set of foundation data for validation tests."""

    # Companies (legal entities)
    c.executemany(
        "INSERT OR REPLACE INTO fo_company (externalCode, startDate, endDate, status, description, country) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("LE-001", "2020-01-01", "9999-12-31", "A", "Legal Entity 001", "CAN"),
            ("LE-002", "2020-01-01", "9999-12-31", "A", "Legal Entity 002", "CAN"),
        ],
    )

    # Business units (no cust_legalEntity column - use junction table)
    c.executemany(
        "INSERT OR REPLACE INTO fo_business_unit (externalCode, startDate, endDate, status, description) "
        "VALUES (?,?,?,?,?)",
        [
            ("BU-001", "2020-01-01", "9999-12-31", "A", "Business Unit 001"),
            ("BU-002", "2020-01-01", "9999-12-31", "A", "Business Unit 002"),
        ],
    )

    # Divisions (no cust_BusinessUnit column - use junction table)
    c.executemany(
        "INSERT OR REPLACE INTO fo_division (externalCode, startDate, endDate, status, description) "
        "VALUES (?,?,?,?,?)",
        [
            ("DIV-001", "2020-01-01", "9999-12-31", "A", "Division 001"),
        ],
    )

    # Departments
    c.executemany(
        "INSERT OR REPLACE INTO fo_department (externalCode, startDate, endDate, status, description, cust_Division) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("DEPT-001", "2020-01-01", "9999-12-31", "A", "Department 001", "DIV-001"),
        ],
    )

    # Sub-departments (standard column names)
    c.executemany(
        "INSERT OR REPLACE INTO cust_sub_department "
        "(externalCode, startDate, endDate, status, externalName_en_US, cust_Department) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("SD-001", "2020-01-01", "9999-12-31", "A", "Sub Dept 001", "DEPT-001"),
        ],
    )

    # Job codes
    c.executemany(
        "INSERT OR REPLACE INTO fo_job_code "
        "(externalCode, startDate, endDate, status, name_en_US, jobFunction, cust_jobsubfunction, grade, cust_careerPath) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                "JC-001",
                "2020-01-01",
                "9999-12-31",
                "A",
                "Job Code 001",
                "JF-001",
                "JSF-001",
                "G5",
                "CP-001",
            ),
        ],
    )

    # Cost centres (no cust_BusinessUnit column - use junction table)
    c.executemany(
        "INSERT OR REPLACE INTO fo_cost_center (externalCode, startDate, endDate, status, description) "
        "VALUES (?,?,?,?,?)",
        [
            ("CC-001", "2020-01-01", "9999-12-31", "A", "Cost Centre 001"),
        ],
    )

    # Locations
    c.executemany(
        "INSERT OR REPLACE INTO fo_location (externalCode, startDate, endDate, status, description) "
        "VALUES (?,?,?,?,?)",
        [
            ("LOC-001", "2020-01-01", "9999-12-31", "A", "Toronto HQ"),
        ],
    )

    # Junction tables
    # BU-001 → LE-001 (only)
    c.execute(
        "INSERT OR REPLACE INTO fo_bu_legal_entity VALUES (?,?)", ("BU-001", "LE-001")
    )
    # BU-002 → LE-001 AND LE-002
    c.execute(
        "INSERT OR REPLACE INTO fo_bu_legal_entity VALUES (?,?)", ("BU-002", "LE-001")
    )
    c.execute(
        "INSERT OR REPLACE INTO fo_bu_legal_entity VALUES (?,?)", ("BU-002", "LE-002")
    )
    # DIV-001 → BU-001 AND BU-002 (multi-BU division)
    c.execute(
        "INSERT OR REPLACE INTO fo_division_business_unit VALUES (?,?)",
        ("DIV-001", "BU-001"),
    )
    c.execute(
        "INSERT OR REPLACE INTO fo_division_business_unit VALUES (?,?)",
        ("DIV-001", "BU-002"),
    )
    # CC-001 → BU-001
    c.execute(
        "INSERT OR REPLACE INTO fo_cost_center_business_unit VALUES (?,?)",
        ("CC-001", "BU-001"),
    )

    c.commit()


def test_junction_tables():
    print("\n--- 4. Junction tables ---")
    c = _conn()
    _insert_synthetic_data(c)
    c.close()

    lookups = build_lookups_from_db()

    # div_to_bus
    assert_eq(
        "DIV-001 maps to {BU-001, BU-002}",
        lookups["div_to_bus"].get("DIV-001"),
        {"BU-001", "BU-002"},
    )
    assert_eq(
        "missing division maps to None", lookups["div_to_bus"].get("DIV-999"), None
    )

    # bu_to_les
    assert_eq("BU-001 maps to {LE-001}", lookups["bu_to_les"].get("BU-001"), {"LE-001"})
    assert_eq(
        "BU-002 maps to {LE-001, LE-002}",
        lookups["bu_to_les"].get("BU-002"),
        {"LE-001", "LE-002"},
    )

    # cc_to_bus
    assert_eq("CC-001 maps to {BU-001}", lookups["cc_to_bus"].get("CC-001"), {"BU-001"})


# ---------------------------------------------------------------------------
# 5. Integrity check logic (pass + fail cases)
# ---------------------------------------------------------------------------


def _make_position(**kwargs):
    """Build a minimal valid position dict, overrideable via kwargs."""
    defaults = {
        "code": "P-001",
        "externalName_en_US": "Test Position",
        "effectiveStartDate": "2024-01-01",
        "effectiveEndDate": "9999-12-31",
        "effectiveStatus": "A",
        "company": "LE-001",
        "businessUnit": "BU-001",
        "division": "DIV-001",
        "department": "DEPT-001",
        "cust_subDepartment": "SD-001",
        "jobCode": "JC-001",
        "costCenter": "CC-001",
        "location": "LOC-001",
        "cust_Country": "CAN",
        "cust_JobFunction": "JF-001",
        "cust_jobSubFunction": "JSF-001",
        "cust_GlobalJobLevel": "G5",
        "cust_CareerPath": "CP-001",
        "payGrade": "",
    }
    defaults.update(kwargs)
    return defaults


def test_check_logic():
    print("\n--- 5. Integrity check logic ---")
    lookups = build_lookups_from_db()

    # --- All passing ---
    issues = validate_positions([_make_position()], lookups)
    assert_eq("valid position → 0 issues", len(issues), 0)

    # CHK-01: subdept maps to wrong department
    issues = validate_positions([_make_position(department="DEPT-WRONG")], lookups)
    chk09 = [i for i in issues if i["Check ID"] == "CHK-01"]
    assert_true("CHK-01 fires when subdept dept mismatch", len(chk09) == 1)
    assert_eq("CHK-01 severity", chk09[0]["Severity"], "CRITICAL")

    # CHK-01: skipped when subdept not in lookup
    issues = validate_positions(
        [_make_position(cust_subDepartment="SD-UNKNOWN", department="DEPT-001")],
        lookups,
    )
    chk09 = [i for i in issues if i["Check ID"] == "CHK-01"]
    assert_eq("CHK-01 skipped when subdept not in lookup", len(chk09), 0)

    # CHK-02: department maps to wrong division
    issues = validate_positions([_make_position(division="DIV-WRONG")], lookups)
    chk10 = [i for i in issues if i["Check ID"] == "CHK-02"]
    assert_true("CHK-02 fires when dept division mismatch", len(chk10) == 1)

    # CHK-03: BU-001 is allowed for DIV-001 → no issue
    issues = validate_positions([_make_position(businessUnit="BU-001")], lookups)
    chk11 = [i for i in issues if i["Check ID"] == "CHK-03"]
    assert_eq("CHK-03 passes for BU-001 in DIV-001", len(chk11), 0)

    # CHK-03: BU-002 is also allowed for DIV-001 → no issue (multi-BU division)
    issues = validate_positions(
        [_make_position(businessUnit="BU-002", company="LE-001")], lookups
    )
    chk11 = [i for i in issues if i["Check ID"] == "CHK-03"]
    assert_eq("CHK-03 passes for BU-002 in DIV-001 (multi-BU)", len(chk11), 0)

    # CHK-03: BU-999 is NOT in DIV-001 → issue
    issues = validate_positions([_make_position(businessUnit="BU-999")], lookups)
    chk11 = [i for i in issues if i["Check ID"] == "CHK-03"]
    assert_true("CHK-03 fires for BU-999 not in DIV-001", len(chk11) == 1)
    assert_true(
        "CHK-03 description contains both allowed BUs",
        "BU-001" in chk11[0]["Issue Description"]
        and "BU-002" in chk11[0]["Issue Description"],
    )

    # CHK-04: LE-001 is allowed for BU-001 → no issue
    issues = validate_positions(
        [_make_position(company="LE-001", businessUnit="BU-001")], lookups
    )
    chk12 = [i for i in issues if i["Check ID"] == "CHK-04"]
    assert_eq("CHK-04 passes for LE-001 in BU-001", len(chk12), 0)

    # CHK-04: LE-002 is NOT in BU-001 → issue
    issues = validate_positions(
        [_make_position(company="LE-002", businessUnit="BU-001")], lookups
    )
    chk12 = [i for i in issues if i["Check ID"] == "CHK-04"]
    assert_true("CHK-04 fires for LE-002 not in BU-001", len(chk12) == 1)

    # CHK-04: LE-002 IS in BU-002 → no issue
    issues = validate_positions(
        [_make_position(company="LE-002", businessUnit="BU-002", division="DIV-001")],
        lookups,
    )
    chk12 = [i for i in issues if i["Check ID"] == "CHK-04"]
    assert_eq("CHK-04 passes for LE-002 in BU-002 (multi-LE)", len(chk12), 0)

    # CHK-05: CC-001 → BU-001 passes
    issues = validate_positions(
        [_make_position(costCenter="CC-001", businessUnit="BU-001")], lookups
    )
    chk13 = [i for i in issues if i["Check ID"] == "CHK-05"]
    assert_eq("CHK-05 passes for CC-001→BU-001", len(chk13), 0)

    # CHK-05: CC-001 → BU-002 fails
    issues = validate_positions(
        [_make_position(costCenter="CC-001", businessUnit="BU-002", company="LE-001")],
        lookups,
    )
    chk13 = [i for i in issues if i["Check ID"] == "CHK-05"]
    assert_true("CHK-05 fires for CC-001→BU-002 mismatch", len(chk13) == 1)

    # CHK-06: job function mismatch
    issues = validate_positions([_make_position(cust_JobFunction="JF-WRONG")], lookups)
    chk15 = [i for i in issues if i["Check ID"] == "CHK-06"]
    assert_true("CHK-06 fires for job function mismatch", len(chk15) == 1)

    # CHK-08: grade mismatch
    issues = validate_positions([_make_position(cust_GlobalJobLevel="G6")], lookups)
    chk17 = [i for i in issues if i["Check ID"] == "CHK-08"]
    assert_true("CHK-08 fires for grade mismatch", len(chk17) == 1)

    # CHK-08: fires even when position GJL is blank (catches missing GJL)
    issues = validate_positions([_make_position(cust_GlobalJobLevel="")], lookups)
    chk17 = [i for i in issues if i["Check ID"] == "CHK-08"]
    assert_true("CHK-08 fires when position GJL is blank", len(chk17) == 1)

    # CHK-07: job sub function mismatch
    issues = validate_positions(
        [_make_position(cust_jobSubFunction="JSF-WRONG")], lookups
    )
    chk07 = [i for i in issues if i["Check ID"] == "CHK-07"]
    assert_true("CHK-07 fires for job sub function mismatch", len(chk07) == 1)
    assert_eq("CHK-07 severity", chk07[0]["Severity"], "HIGH")

    # CHK-07: passes when job sub function matches
    issues = validate_positions(
        [_make_position(cust_jobSubFunction="JSF-001")], lookups
    )
    chk07 = [i for i in issues if i["Check ID"] == "CHK-07"]
    assert_eq("CHK-07 passes when job sub function matches", len(chk07), 0)

    # CHK-09: career path mismatch
    issues = validate_positions([_make_position(cust_CareerPath="CP-WRONG")], lookups)
    chk09 = [i for i in issues if i["Check ID"] == "CHK-09"]
    assert_true("CHK-09 fires for career path mismatch", len(chk09) == 1)
    assert_eq("CHK-09 severity", chk09[0]["Severity"], "HIGH")

    # CHK-09: passes when career path matches
    issues = validate_positions([_make_position(cust_CareerPath="CP-001")], lookups)
    chk09 = [i for i in issues if i["Check ID"] == "CHK-09"]
    assert_eq("CHK-09 passes when career path matches", len(chk09), 0)


# ---------------------------------------------------------------------------
# 5b. Foundation active checks (CHK-10 to CHK-17)
# ---------------------------------------------------------------------------


def test_foundation_active_checks():
    print("\n--- 5b. Foundation active checks ---")
    c = _conn()

    # Insert an inactive company (status=I, ended in past)
    c.executemany(
        "INSERT OR REPLACE INTO fo_company (externalCode, startDate, endDate, status, description, country) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("LE-INACTIVE", "2020-01-01", "2020-12-31", "I", "Inactive Entity", "CAN"),
        ],
    )

    # Insert an inactive business unit (status=I)
    c.executemany(
        "INSERT OR REPLACE INTO fo_business_unit (externalCode, startDate, endDate, status, description) "
        "VALUES (?,?,?,?,?)",
        [
            ("BU-INACTIVE", "2020-01-01", "2020-12-31", "I", "Inactive BU"),
        ],
    )

    # Insert a future-dated company (not yet active)
    c.executemany(
        "INSERT OR REPLACE INTO fo_company (externalCode, startDate, endDate, status, description, country) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("LE-FUTURE", "2030-01-01", "9999-12-31", "A", "Future Entity", "CAN"),
        ],
    )

    # Insert an inactive division, department, sub department, job code,
    # cost centre, and location. The CHK-12..16 tests below need them.
    c.executemany(
        "INSERT OR REPLACE INTO fo_division (externalCode, startDate, endDate, status, description) "
        "VALUES (?,?,?,?,?)",
        [
            ("DIV-INACTIVE", "2020-01-01", "2020-12-31", "I", "Inactive Division"),
        ],
    )
    c.executemany(
        "INSERT OR REPLACE INTO fo_department (externalCode, startDate, endDate, status, description, cust_Division) "
        "VALUES (?,?,?,?,?,?)",
        [
            (
                "DEPT-INACTIVE",
                "2020-01-01",
                "2020-12-31",
                "I",
                "Inactive Department",
                "DIV-001",
            ),
        ],
    )
    c.executemany(
        "INSERT OR REPLACE INTO cust_sub_department (externalCode, startDate, endDate, status, externalName_en_US, cust_Department) "
        "VALUES (?,?,?,?,?,?)",
        [
            (
                "SD-INACTIVE",
                "2020-01-01",
                "2020-12-31",
                "I",
                "Inactive Sub Dept",
                "DEPT-001",
            ),
        ],
    )
    c.executemany(
        "INSERT OR REPLACE INTO fo_job_code (externalCode, startDate, endDate, status, name_en_US, jobFunction, cust_jobsubfunction, grade, cust_careerPath) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                "JC-INACTIVE",
                "2020-01-01",
                "2020-12-31",
                "I",
                "Inactive Job Code",
                "JF-001",
                "JSF-001",
                "GJL-001",
                "CP-001",
            ),
        ],
    )
    c.executemany(
        "INSERT OR REPLACE INTO fo_cost_center (externalCode, startDate, endDate, status, description) "
        "VALUES (?,?,?,?,?)",
        [
            ("CC-INACTIVE", "2020-01-01", "2020-12-31", "I", "Inactive Cost Centre"),
        ],
    )
    c.executemany(
        "INSERT OR REPLACE INTO fo_location (externalCode, startDate, endDate, status, description) "
        "VALUES (?,?,?,?,?)",
        [
            ("LOC-INACTIVE", "2020-01-01", "2020-12-31", "I", "Inactive Location"),
        ],
    )

    # Boundary cases for CHK-10 - entity whose endDate == as_of_date should
    # NOT be active on the run date (end-of-day semantics). Insert one to
    # confirm the boundary is handled correctly.
    c.executemany(
        "INSERT OR REPLACE INTO fo_company (externalCode, startDate, endDate, status, description, country) "
        "VALUES (?,?,?,?,?,?)",
        [
            (
                "LE-END-ON-ASOF",
                "2020-01-01",
                "2024-06-01",
                "A",
                "Ends on as_of date",
                "CAN",
            ),
        ],
    )
    # Entity whose startDate == as_of_date SHOULD be active (start-of-day).
    c.executemany(
        "INSERT OR REPLACE INTO fo_company (externalCode, startDate, endDate, status, description, country) "
        "VALUES (?,?,?,?,?,?)",
        [
            (
                "LE-START-ON-ASOF",
                "2024-06-01",
                "9999-12-31",
                "A",
                "Starts on as_of date",
                "CAN",
            ),
        ],
    )

    c.commit()
    c.close()

    lookups = build_lookups_from_db()
    as_of_date = datetime.date(2024, 6, 1)

    # CHK-10: inactive legal entity
    issues = validate_positions(
        [_make_position(company="LE-INACTIVE")], lookups, as_of_date=as_of_date
    )
    chk10 = [i for i in issues if i["Check ID"] == "CHK-10"]
    assert_true("CHK-10 fires for inactive legal entity", len(chk10) == 1)
    assert_eq("CHK-10 severity", chk10[0]["Severity"], "CRITICAL")
    assert_true(
        "CHK-10 description mentions inactive status",
        "not active" in chk10[0]["Issue Description"].lower(),
    )

    # CHK-10: future-dated legal entity (not active as-of today)
    issues = validate_positions(
        [_make_position(company="LE-FUTURE")], lookups, as_of_date=as_of_date
    )
    chk10 = [i for i in issues if i["Check ID"] == "CHK-10"]
    assert_true("CHK-10 fires for future-dated legal entity", len(chk10) == 1)

    # CHK-10: passes for active legal entity
    issues = validate_positions(
        [_make_position(company="LE-001")], lookups, as_of_date=as_of_date
    )
    chk10 = [i for i in issues if i["Check ID"] == "CHK-10"]
    assert_eq("CHK-10 passes for active legal entity", len(chk10), 0)

    # CHK-10: boundary - the validator uses inclusive comparison
    # (startDate <= as_of_date <= endDate) which means an entity ending on
    # the as_of_date is still considered active. Documenting this here so
    # the behaviour is intentional rather than accidental.
    issues = validate_positions(
        [_make_position(company="LE-END-ON-ASOF")], lookups, as_of_date=as_of_date
    )
    chk10 = [i for i in issues if i["Check ID"] == "CHK-10"]
    assert_eq(
        "CHK-10 inclusive endDate: entity ending on as_of_date IS active", len(chk10), 0
    )

    # CHK-10: boundary - endDate one day BEFORE as_of_date is NOT active.
    # Inserting a fresh entity to keep the test self-contained.
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO fo_company (externalCode, startDate, endDate, status, description, country) "
        "VALUES (?,?,?,?,?,?)",
        (
            "LE-ENDED-YESTERDAY",
            "2020-01-01",
            "2024-05-31",
            "A",
            "Ended day before as_of",
            "CAN",
        ),
    )
    c.commit()
    c.close()
    lookups = build_lookups_from_db()
    issues = validate_positions(
        [_make_position(company="LE-ENDED-YESTERDAY")], lookups, as_of_date=as_of_date
    )
    chk10 = [i for i in issues if i["Check ID"] == "CHK-10"]
    assert_true(
        "CHK-10 fires when endDate is one day before as_of_date", len(chk10) == 1
    )

    # CHK-10: boundary - startDate == as_of_date IS active (start-of-day)
    issues = validate_positions(
        [_make_position(company="LE-START-ON-ASOF")], lookups, as_of_date=as_of_date
    )
    chk10 = [i for i in issues if i["Check ID"] == "CHK-10"]
    assert_eq(
        "CHK-10 passes when startDate == as_of_date (start-of-day boundary)",
        len(chk10),
        0,
    )

    # CHK-11: inactive business unit
    issues = validate_positions(
        [_make_position(businessUnit="BU-INACTIVE")], lookups, as_of_date=as_of_date
    )
    chk11 = [i for i in issues if i["Check ID"] == "CHK-11"]
    assert_true("CHK-11 fires for inactive business unit", len(chk11) == 1)
    assert_eq("CHK-11 severity", chk11[0]["Severity"], "CRITICAL")

    # CHK-11: passes for active business unit
    issues = validate_positions(
        [_make_position(businessUnit="BU-001")], lookups, as_of_date=as_of_date
    )
    chk11 = [i for i in issues if i["Check ID"] == "CHK-11"]
    assert_eq("CHK-11 passes for active business unit", len(chk11), 0)

    # CHK-12: inactive division
    issues = validate_positions(
        [_make_position(division="DIV-INACTIVE")], lookups, as_of_date=as_of_date
    )
    chk12 = [i for i in issues if i["Check ID"] == "CHK-12"]
    assert_true("CHK-12 fires for inactive division", len(chk12) == 1)
    assert_eq("CHK-12 severity", chk12[0]["Severity"], "CRITICAL")

    # CHK-12: passes for active division
    issues = validate_positions(
        [_make_position(division="DIV-001")], lookups, as_of_date=as_of_date
    )
    chk12 = [i for i in issues if i["Check ID"] == "CHK-12"]
    assert_eq("CHK-12 passes for active division", len(chk12), 0)

    # CHK-13: inactive department
    issues = validate_positions(
        [_make_position(department="DEPT-INACTIVE")], lookups, as_of_date=as_of_date
    )
    chk13 = [i for i in issues if i["Check ID"] == "CHK-13"]
    assert_true("CHK-13 fires for inactive department", len(chk13) == 1)
    assert_eq("CHK-13 severity", chk13[0]["Severity"], "CRITICAL")

    # CHK-13: passes for active department
    issues = validate_positions(
        [_make_position(department="DEPT-001")], lookups, as_of_date=as_of_date
    )
    chk13 = [i for i in issues if i["Check ID"] == "CHK-13"]
    assert_eq("CHK-13 passes for active department", len(chk13), 0)

    # CHK-14: inactive sub department
    issues = validate_positions(
        [_make_position(cust_subDepartment="SD-INACTIVE")],
        lookups,
        as_of_date=as_of_date,
    )
    chk14 = [i for i in issues if i["Check ID"] == "CHK-14"]
    assert_true("CHK-14 fires for inactive sub department", len(chk14) == 1)
    assert_eq("CHK-14 severity", chk14[0]["Severity"], "CRITICAL")

    # CHK-14: passes for active sub department
    issues = validate_positions(
        [_make_position(cust_subDepartment="SD-001")], lookups, as_of_date=as_of_date
    )
    chk14 = [i for i in issues if i["Check ID"] == "CHK-14"]
    assert_eq("CHK-14 passes for active sub department", len(chk14), 0)

    # CHK-15: inactive job code
    issues = validate_positions(
        [_make_position(jobCode="JC-INACTIVE")], lookups, as_of_date=as_of_date
    )
    chk15 = [i for i in issues if i["Check ID"] == "CHK-15"]
    assert_true("CHK-15 fires for inactive job code", len(chk15) == 1)
    assert_eq("CHK-15 severity", chk15[0]["Severity"], "CRITICAL")

    # CHK-15: passes for active job code
    issues = validate_positions(
        [_make_position(jobCode="JC-001")], lookups, as_of_date=as_of_date
    )
    chk15 = [i for i in issues if i["Check ID"] == "CHK-15"]
    assert_eq("CHK-15 passes for active job code", len(chk15), 0)

    # CHK-16: inactive cost centre
    issues = validate_positions(
        [_make_position(costCenter="CC-INACTIVE")], lookups, as_of_date=as_of_date
    )
    chk16 = [i for i in issues if i["Check ID"] == "CHK-16"]
    assert_true("CHK-16 fires for inactive cost centre", len(chk16) == 1)
    assert_eq("CHK-16 severity", chk16[0]["Severity"], "CRITICAL")

    # CHK-16: passes for active cost centre
    issues = validate_positions(
        [_make_position(costCenter="CC-001")], lookups, as_of_date=as_of_date
    )
    chk16 = [i for i in issues if i["Check ID"] == "CHK-16"]
    assert_eq("CHK-16 passes for active cost centre", len(chk16), 0)

    # CHK-17: inactive location
    issues = validate_positions(
        [_make_position(location="LOC-INACTIVE")], lookups, as_of_date=as_of_date
    )
    chk17 = [i for i in issues if i["Check ID"] == "CHK-17"]
    assert_true("CHK-17 fires for inactive location", len(chk17) == 1)
    assert_eq("CHK-17 severity", chk17[0]["Severity"], "CRITICAL")

    # CHK-17: passes for active location
    issues = validate_positions(
        [_make_position(location="LOC-001")], lookups, as_of_date=as_of_date
    )
    chk17 = [i for i in issues if i["Check ID"] == "CHK-17"]
    assert_eq("CHK-17 passes for active location", len(chk17), 0)


# ---------------------------------------------------------------------------
# 6. validation_results save/load with FK
# ---------------------------------------------------------------------------


def test_validation_results_save():
    print("\n--- 6. validation_results save/load ---")

    # Insert extract_meta record
    meta_id = db.save_extract_meta("CAN", 1, complete=True)
    assert_true(
        "save_extract_meta returns int meta_id",
        isinstance(meta_id, int) and meta_id > 0,
    )

    # Build an issue dict (as produced by validators._issue)
    issue = {
        "Position ID": "P-001",
        "Position Title": "Test Position",
        "Effective Start Date": "2024-01-01",
        "Legal Entity": "LE-001",
        "Business Unit": "BU-001",
        "Division": "DIV-001",
        "Department": "DEPT-001",
        "Sub Department": "SD-001",
        "Job Code": "JC-001",
        "Cost Centre": "CC-001",
        "Location": "LOC-001",
        "Check ID": "CHK-01",
        "Check Category": "Hierarchy Alignment",
        "Failed Field": "department",
        "Issue Description": "Sub Department 'SD-001' belongs to Department 'DEPT-001' ...",
        "Severity": "CRITICAL",
    }

    db.save_validation_results([issue], "2024-01-01T12:00:00", meta_id)

    rows = db.load_table("validation_results")
    assert_true("1 row saved to validation_results", len(rows) >= 1)

    last = rows[-1]
    assert_eq("position_code saved", last["position_code"], "P-001")
    assert_eq("check_id saved", last["check_id"], "CHK-01")
    assert_eq("severity saved", last["severity"], "CRITICAL")
    assert_eq("extract_meta_id FK set", last["extract_meta_id"], meta_id)
    # Snapshot columns removed - these must NOT be present
    assert_true("company NOT in validation_results row", "company" not in last)
    assert_true(
        "businessUnit NOT in validation_results row", "businessUnit" not in last
    )

    # FK violation: invalid meta_id must be rejected with FK pragma ON
    import sqlite3 as _sql

    assert_raises(
        "FK violation raises IntegrityError",
        _sql.IntegrityError,
        lambda: db.save_validation_results([issue], "2024-01-01T12:00:00", 99999),
    )


# ---------------------------------------------------------------------------
# 7. Audit views
# ---------------------------------------------------------------------------


def test_audit_views():
    print("\n--- 7. Audit views ---")
    c = _conn()

    # chk01_failures: should find the intentional mismatch (SD-001 → DEPT-001 but position has DEPT-WRONG)
    c.execute(
        "INSERT OR REPLACE INTO positions (code, cust_subDepartment, department) "
        "VALUES ('VIEW-TEST-P1', 'SD-001', 'DEPT-WRONG')"
    )
    c.commit()
    rows = c.execute(
        "SELECT * FROM chk01_failures WHERE position_code='VIEW-TEST-P1'"
    ).fetchall()
    assert_true("chk01_failures detects SD-001/DEPT-WRONG mismatch", len(rows) == 1)

    # chk01_failures: correct position should not appear
    c.execute(
        "INSERT OR REPLACE INTO positions (code, cust_subDepartment, department) "
        "VALUES ('VIEW-TEST-P2', 'SD-001', 'DEPT-001')"
    )
    c.commit()
    rows = c.execute(
        "SELECT * FROM chk01_failures WHERE position_code='VIEW-TEST-P2'"
    ).fetchall()
    assert_eq("chk01_failures: correct position not flagged", len(rows), 0)

    # chk03_failures: BU-999 not in DIV-001
    c.execute(
        "INSERT OR REPLACE INTO positions (code, division, businessUnit) "
        "VALUES ('VIEW-TEST-P3', 'DIV-001', 'BU-999')"
    )
    c.commit()
    rows = c.execute(
        "SELECT * FROM chk03_failures WHERE position_code='VIEW-TEST-P3'"
    ).fetchall()
    assert_true("chk03_failures detects BU-999 not in DIV-001", len(rows) == 1)

    # chk04_failures: LE-002 not in BU-001
    c.execute(
        "INSERT OR REPLACE INTO positions (code, businessUnit, company) "
        "VALUES ('VIEW-TEST-P4', 'BU-001', 'LE-002')"
    )
    c.commit()
    rows = c.execute(
        "SELECT * FROM chk04_failures WHERE position_code='VIEW-TEST-P4'"
    ).fetchall()
    assert_true("chk04_failures detects LE-002 not in BU-001", len(rows) == 1)

    c.close()


# ---------------------------------------------------------------------------
# 8. save_pipe_sep_junctions directly
# ---------------------------------------------------------------------------


def test_save_pipe_sep_junctions():
    print("\n--- 8. save_pipe_sep_junctions ---")

    # Insert a new division and populate its junctions
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO fo_division (externalCode, startDate, endDate, status, description) "
        "VALUES ('DIV-PIPE', '2020-01-01', '9999-12-31', 'A', 'Pipe Sep Test Division')"
    )
    c.commit()
    c.close()

    # Simulate what fetchers.py does: record still has raw pipe-sep value
    records = [
        {"externalCode": "DIV-PIPE", "cust_BusinessUnit": "BU-001|BU-002|BU-003"}
    ]
    db.save_pipe_sep_junctions(
        "fo_division_business_unit",
        "division_code",
        "bu_code",
        records,
        "cust_BusinessUnit",
    )

    c = _conn()
    rows = c.execute(
        "SELECT bu_code FROM fo_division_business_unit WHERE division_code='DIV-PIPE' ORDER BY bu_code"
    ).fetchall()
    c.close()
    bu_codes = {r[0] for r in rows}
    assert_eq(
        "pipe-sep '|' produces 3 junction rows",
        bu_codes,
        {"BU-001", "BU-002", "BU-003"},
    )

    # Empty pipe-sep string → no rows inserted
    records_empty = [{"externalCode": "DIV-PIPE", "cust_BusinessUnit": ""}]
    db.save_pipe_sep_junctions(
        "fo_division_business_unit",
        "division_code",
        "bu_code",
        records_empty,
        "cust_BusinessUnit",
    )
    # Still 3 rows (empty didn't add more, and INSERT OR REPLACE on existing PK)
    c = _conn()
    count = c.execute(
        "SELECT COUNT(*) FROM fo_division_business_unit WHERE division_code='DIV-PIPE'"
    ).fetchone()[0]
    c.close()
    assert_eq("empty pipe-sep doesn't add rows", count, 3)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main():
    print("=" * 60)
    print("  SF Position Integrity Checker - Schema Test Suite")
    print("=" * 60)

    # Initialise a fresh test database
    print("\n[SETUP] Initialising test database...")
    db.init_db()

    test_schema_structure()
    test_check_constraints()
    test_date_normalisation()
    test_junction_tables()
    test_check_logic()
    test_foundation_active_checks()
    test_validation_results_save()
    test_audit_views()
    test_save_pipe_sep_junctions()

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"  Results: {_PASS}/{total} passed, {_FAIL} failed")
    print("=" * 60)

    if _FAIL > 0:
        print("\n[FAIL] Some tests failed - see output above.")
        sys.exit(1)
    else:
        print("\n[PASS] All tests passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
