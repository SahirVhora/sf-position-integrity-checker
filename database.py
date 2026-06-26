"""
database.py - SQLite local store for SF Position Integrity Checker.

DB file:  ./data/sf_integrity_{COUNTRY}.db  (set at runtime via set_country())

On Extract run : wipe and recreate all tables (init_db).
On Validate run: connect read-only, fail gracefully if DB absent.

Column names deliberately match SF OData API field names so that
record dicts can be inserted/loaded without a remapping layer,
with the exception of cust_sub_department which uses standard
startDate/endDate/status names (aliased from SF-specific names
in fetchers.py before saving).
"""

import os
import re as _re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "sf_integrity_CA.db")


def set_country(country: str) -> None:
    """Point DB_PATH at the country-specific database file."""
    global DB_PATH
    DB_PATH = os.path.join(DB_DIR, f"sf_integrity_{country.upper()}.db")


# Matches column names ending in "Date" or "date" (e.g. startDate, effectiveStartDate)
_DATE_COL_RE = _re.compile(r"[Dd]ate$")

# ---------------------------------------------------------------------------
# Date normalisation helper
# ---------------------------------------------------------------------------


def normalise_date(raw: str) -> str:
    """
    Normalise SF OData date values to YYYY-MM-DD for consistent DB storage.

    Handles:
      - SF epoch-millis  /Date(1609459200000)/  or  /Date(-2208988800000)/
      - ISO datetime     2024-01-15T00:00:00   (truncated to date)
      - ISO date         2024-01-15             (passed through)
      - Empty / None     returned as-is

    Returns the original string if it cannot be parsed.
    """
    if not raw or not raw.strip():
        return raw
    s = raw.strip()
    if s.startswith("/Date("):
        inner = s[6:].split(")")[0]
        ms_str = inner
        for i in range(1, len(inner)):
            if inner[i] in ("+", "-"):
                ms_str = inner[:i]
                break
        try:
            ms = int(ms_str)
            # Avoid platform-dependent fromtimestamp() failures for negative/large epochs.
            d = date(1970, 1, 1) + timedelta(days=(ms // 86_400_000))
            return d.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            try:
                return (
                    date.min.strftime("%Y-%m-%d")
                    if int(ms_str) < 0
                    else date.max.strftime("%Y-%m-%d")
                )
            except ValueError:
                return raw
    # Already ISO - truncate to date part
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
-- Run history
CREATE TABLE extract_meta (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_timestamp     TEXT NOT NULL,
    country           TEXT NOT NULL,
    positions_fetched INTEGER,
    extract_complete  INTEGER NOT NULL DEFAULT 0 CHECK(extract_complete IN (0, 1))
);

-- Main positions entity
CREATE TABLE positions (
    code                  TEXT PRIMARY KEY CHECK(length(code) > 0),
    externalName_en_US    TEXT,
    effectiveStartDate    TEXT,
    effectiveEndDate      TEXT,
    effectiveStatus       TEXT,
    company               TEXT,
    businessUnit          TEXT,
    division              TEXT,
    department            TEXT,
    cust_subDepartment    TEXT,
    jobCode               TEXT,
    costCenter            TEXT,
    location              TEXT,
    cust_Country          TEXT,
    cust_JobFunction      TEXT,
    cust_jobSubFunction   TEXT,
    cust_GlobalJobLevel   TEXT,
    cust_CareerPath       TEXT,
    payGrade              TEXT
);

-- Foundation Objects (Lookup Tables)
-- cust_legalEntity removed - see fo_bu_legal_entity junction table
CREATE TABLE fo_company (
    externalCode  TEXT PRIMARY KEY,
    startDate     TEXT,
    endDate       TEXT,
    status        TEXT CHECK(status IN ('A', 'I') OR status IS NULL),
    description   TEXT,
    country       TEXT
);

-- cust_legalEntity removed - see fo_bu_legal_entity junction table
CREATE TABLE fo_business_unit (
    externalCode  TEXT PRIMARY KEY,
    startDate     TEXT,
    endDate       TEXT,
    status        TEXT CHECK(status IN ('A', 'I') OR status IS NULL),
    description   TEXT
);

-- cust_BusinessUnit removed - see fo_division_business_unit junction table
CREATE TABLE fo_division (
    externalCode  TEXT PRIMARY KEY,
    startDate     TEXT,
    endDate       TEXT,
    status        TEXT CHECK(status IN ('A', 'I') OR status IS NULL),
    description   TEXT
);

CREATE TABLE fo_department (
    externalCode  TEXT PRIMARY KEY,
    startDate     TEXT,
    endDate       TEXT,
    status        TEXT CHECK(status IN ('A', 'I') OR status IS NULL),
    description   TEXT,
    cust_Division TEXT
);

-- Column names standardised from SF-specific names:
--   effectiveStartDate         -> startDate
--   mdfSystemEffectiveEndDate  -> endDate
--   mdfSystemStatus            -> status
CREATE TABLE cust_sub_department (
    externalCode       TEXT PRIMARY KEY,
    startDate          TEXT,
    endDate            TEXT,
    status             TEXT CHECK(status IN ('A', 'I') OR status IS NULL),
    externalName_en_US TEXT,
    cust_Department    TEXT
);

CREATE TABLE fo_job_code (
    externalCode        TEXT PRIMARY KEY,
    startDate           TEXT,
    endDate             TEXT,
    status              TEXT CHECK(status IN ('A', 'I') OR status IS NULL),
    name_en_US          TEXT,
    jobFunction         TEXT,
    cust_jobsubfunction TEXT,
    grade               TEXT,
    cust_careerPath     TEXT
);

CREATE TABLE fo_job_class_local_can (
    externalCode       TEXT PRIMARY KEY,
    startDate          TEXT,
    endDate            TEXT,
    status             TEXT CHECK(status IN ('A', 'I') OR status IS NULL),
    cust_LocalJobLevel TEXT,
    country            TEXT
);

-- cust_BusinessUnit removed - see fo_cost_center_business_unit junction table
CREATE TABLE fo_cost_center (
    externalCode  TEXT PRIMARY KEY,
    startDate     TEXT,
    endDate       TEXT,
    status        TEXT CHECK(status IN ('A', 'I') OR status IS NULL),
    description   TEXT
);

CREATE TABLE fo_location (
    externalCode TEXT PRIMARY KEY,
    startDate    TEXT,
    endDate      TEXT,
    status       TEXT CHECK(status IN ('A', 'I') OR status IS NULL),
    description  TEXT
);

-- ---------------------------------------------------------------------------
-- Junction tables replacing pipe-separated many-to-many columns
-- No FK constraints: BU codes in division links may not all be fetched
-- (we only fetch BUs referenced directly by positions).
-- ---------------------------------------------------------------------------

CREATE TABLE fo_division_business_unit (
    division_code TEXT NOT NULL,
    bu_code       TEXT NOT NULL,
    PRIMARY KEY (division_code, bu_code)
);

CREATE TABLE fo_bu_legal_entity (
    bu_code           TEXT NOT NULL,
    legal_entity_code TEXT NOT NULL,
    PRIMARY KEY (bu_code, legal_entity_code)
);

CREATE TABLE fo_cost_center_business_unit (
    cost_center_code TEXT NOT NULL,
    bu_code          TEXT NOT NULL,
    PRIMARY KEY (cost_center_code, bu_code)
);

-- ---------------------------------------------------------------------------
-- Validation Results - redesigned schema
-- Snapshot columns for company/businessUnit/division/department/etc. removed:
--   join to positions table for those values.
-- Only position_title and effectiveStartDate kept as minimal snapshot for
-- historical queries (positions table is wiped on each extract).
-- ---------------------------------------------------------------------------
CREATE TABLE validation_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    extract_meta_id   INTEGER NOT NULL REFERENCES extract_meta(id) ON DELETE CASCADE,
    run_timestamp     TEXT NOT NULL,
    position_code     TEXT NOT NULL,
    position_title    TEXT,
    effectiveStartDate TEXT,
    check_id          TEXT NOT NULL,
    check_category    TEXT NOT NULL,
    failed_field      TEXT NOT NULL,
    issue_description TEXT NOT NULL,
    severity          TEXT NOT NULL CHECK(severity IN ('CRITICAL','HIGH','MEDIUM','LOW'))
);

CREATE INDEX idx_vr_extract      ON validation_results(extract_meta_id);
CREATE INDEX idx_vr_run_severity ON validation_results(run_timestamp, severity);
CREATE INDEX idx_vr_check_id     ON validation_results(check_id);
CREATE INDEX idx_vr_position     ON validation_results(position_code);

-- Current employee assignment per position (sourced from EmpJob)
CREATE TABLE emp_job (
    position_code  TEXT PRIMARY KEY,
    userId         TEXT,
    emplStatus     TEXT,
    startDate      TEXT
);

-- ---------------------------------------------------------------------------
-- Audit views - SQL equivalents of CHK-01, CHK-03, CHK-04 for inspection
-- ---------------------------------------------------------------------------

CREATE VIEW chk01_failures AS
SELECT
    p.code               AS position_code,
    p.externalName_en_US AS position_title,
    p.cust_subDepartment AS position_subdept,
    p.department         AS position_dept,
    sd.cust_Department   AS subdept_mapped_dept
FROM positions p
INNER JOIN cust_sub_department sd ON sd.externalCode = p.cust_subDepartment
WHERE p.department IS NOT NULL AND p.department != ''
  AND sd.cust_Department IS NOT NULL AND sd.cust_Department != ''
  AND sd.cust_Department != p.department;

CREATE VIEW chk03_failures AS
SELECT
    p.code               AS position_code,
    p.externalName_en_US AS position_title,
    p.division           AS position_division,
    p.businessUnit       AS position_bu
FROM positions p
INNER JOIN fo_division d ON d.externalCode = p.division
WHERE p.businessUnit IS NOT NULL AND p.businessUnit != ''
  AND p.businessUnit NOT IN (
      SELECT bu_code FROM fo_division_business_unit
      WHERE division_code = p.division
  );

CREATE VIEW chk04_failures AS
SELECT
    p.code               AS position_code,
    p.externalName_en_US AS position_title,
    p.businessUnit       AS position_bu,
    p.company            AS position_company
FROM positions p
INNER JOIN fo_business_unit bu ON bu.externalCode = p.businessUnit
WHERE p.company IS NOT NULL AND p.company != ''
  AND p.company NOT IN (
      SELECT legal_entity_code FROM fo_bu_legal_entity
      WHERE bu_code = p.businessUnit
  );
"""

# Ordered column lists per table - used for INSERT statements.
# Junction tables are handled separately via save_pipe_sep_junctions().
_TABLE_COLS: dict[str, list[str]] = {
    "positions": [
        "code",
        "externalName_en_US",
        "effectiveStartDate",
        "effectiveEndDate",
        "effectiveStatus",
        "company",
        "businessUnit",
        "division",
        "department",
        "cust_subDepartment",
        "jobCode",
        "costCenter",
        "location",
        "cust_Country",
        "cust_JobFunction",
        "cust_jobSubFunction",
        "cust_GlobalJobLevel",
        "cust_CareerPath",
        "payGrade",
    ],
    "fo_company": [
        "externalCode",
        "startDate",
        "endDate",
        "status",
        "description",
        "country",
    ],
    # cust_legalEntity removed - see fo_bu_legal_entity junction table
    "fo_business_unit": [
        "externalCode",
        "startDate",
        "endDate",
        "status",
        "description",
    ],
    # cust_BusinessUnit removed - see fo_division_business_unit junction table
    "fo_division": [
        "externalCode",
        "startDate",
        "endDate",
        "status",
        "description",
    ],
    "fo_department": [
        "externalCode",
        "startDate",
        "endDate",
        "status",
        "description",
        "cust_Division",
    ],
    # Uses standardised names (startDate/endDate/status) aliased in fetchers.py
    "cust_sub_department": [
        "externalCode",
        "startDate",
        "endDate",
        "status",
        "externalName_en_US",
        "cust_Department",
    ],
    "fo_job_code": [
        "externalCode",
        "startDate",
        "endDate",
        "status",
        "name_en_US",
        "jobFunction",
        "cust_jobsubfunction",
        "grade",
        "cust_careerPath",
    ],
    "fo_job_class_local_can": [
        "externalCode",
        "startDate",
        "endDate",
        "status",
        "cust_LocalJobLevel",
        "country",
    ],
    # cust_BusinessUnit removed - see fo_cost_center_business_unit junction table
    "fo_cost_center": [
        "externalCode",
        "startDate",
        "endDate",
        "status",
        "description",
    ],
    "fo_location": [
        "externalCode",
        "startDate",
        "endDate",
        "status",
        "description",
    ],
    "emp_job": [
        "position_code",
        "userId",
        "emplStatus",
        "startDate",
    ],
}

# Columns saved to validation_results (stale snapshot fields removed)
_VALIDATION_COLS = [
    "extract_meta_id",
    "run_timestamp",
    "position_code",
    "position_title",
    "effectiveStartDate",
    "check_id",
    "check_category",
    "failed_field",
    "issue_description",
    "severity",
]

# Map from reporters.py issue-dict keys → validation_results column names
_ISSUE_KEY_MAP = {
    "Position ID": "position_code",
    "Position Title": "position_title",
    "Effective Start Date": "effectiveStartDate",
    "Check ID": "check_id",
    "Check Category": "check_category",
    "Failed Field": "failed_field",
    "Issue Description": "issue_description",
    "Severity": "severity",
}

# Known tables and views for identifier validation
_KNOWN_TABLES = {
    *_TABLE_COLS.keys(),
    "fo_division_business_unit",
    "fo_bu_legal_entity",
    "fo_cost_center_business_unit",
    "extract_meta",
    "validation_results",
}
_KNOWN_VIEWS = {"chk01_failures", "chk03_failures", "chk04_failures"}


def _validate_sql_identifier(name: str) -> None:
    """Verify a string is a safe SQL identifier (alphanumeric + underscore only)."""
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"Invalid SQL identifier: {name!r}")


def _validate_table_name(name: str) -> None:
    """Verify table/view name is known."""
    if name not in _KNOWN_TABLES and name not in _KNOWN_VIEWS:
        raise ValueError(f"Unknown table or view: {name!r}")


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def get_connection(read_only: bool = False) -> sqlite3.Connection:
    """Return a sqlite3 connection. Raises FileNotFoundError if DB missing in read-only mode."""
    if read_only and not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            "No database found. Please run Option 1 (Extract & Validate) "
            "or Option 3 (Only Extract) first."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Wipe and recreate all tables. Creates ./data/ if needed."""
    os.makedirs(DB_DIR, exist_ok=True)
    # Connect directly (no read-only check - we're creating it)
    # FKs deliberately OFF during init so DROP order doesn't matter
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Drop views first (they depend on tables)
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name NOT LIKE 'sqlite_%'"
    )
    for (vname,) in cur.fetchall():
        _validate_sql_identifier(vname)
        conn.execute(f"DROP VIEW IF EXISTS [{vname}]")
    # Drop all user tables
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    for (tname,) in cur.fetchall():
        _validate_sql_identifier(tname)
        conn.execute(f"DROP TABLE IF EXISTS [{tname}]")
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    conn.close()
    print(f"  [DB] Initialised database at {DB_PATH}")


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------


def _norm_val(col: str, raw: Any) -> str:
    """Stringify a value, normalising date columns to YYYY-MM-DD."""
    s = str(raw or "") if raw is not None else ""
    if s and _DATE_COL_RE.search(col):
        return normalise_date(s)
    return s


def _bulk_insert(
    conn: sqlite3.Connection,
    table: str,
    cols: list[str],
    records: list[dict[str, Any]],
) -> None:
    if not records:
        return
    placeholders = ", ".join("?" for _ in cols)
    col_str = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})"
    rows = [tuple(_norm_val(c, r.get(c)) for c in cols) for r in records]
    conn.executemany(sql, rows)
    conn.commit()


def save_positions(records: list[dict[str, Any]]) -> None:
    conn = get_connection()
    try:
        _bulk_insert(conn, "positions", _TABLE_COLS["positions"], records)
    finally:
        conn.close()


def save_foundation(table_name: str, records: list[dict[str, Any]]) -> None:
    cols = _TABLE_COLS.get(table_name)
    if cols is None:
        raise ValueError(f"Unknown foundation table: {table_name}")
    conn = get_connection()
    try:
        _bulk_insert(conn, table_name, cols, records)
    finally:
        conn.close()


def save_pipe_sep_junctions(
    junction_table: str,
    parent_col: str,
    child_col: str,
    records: list[dict[str, Any]],
    source_field: str,
) -> None:
    """
    Populate a junction table from pipe-separated codes stored in source records.

    Args:
        junction_table : target junction table name
        parent_col     : column name for the parent entity code
        child_col      : column name for the child entity code
        records        : parent records (each has externalCode + pipe-sep source_field)
        source_field   : field in records containing pipe-separated codes
    """
    rows = []
    for rec in records:
        parent_code = rec.get("externalCode")
        if not parent_code:
            continue
        raw = rec.get(source_field) or ""
        for code in raw.split("|"):
            code = code.strip()
            if code:
                rows.append((parent_code, code))
    if not rows:
        return
    _validate_table_name(junction_table)
    _validate_sql_identifier(parent_col)
    _validate_sql_identifier(child_col)
    conn = get_connection()
    try:
        conn.executemany(
            f"INSERT OR REPLACE INTO {junction_table} ({parent_col}, {child_col}) VALUES (?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def save_validation_results(
    results: list[dict[str, Any]],
    run_timestamp: str,
    meta_id: int,
) -> None:
    db_records = []
    for r in results:
        row: dict[str, Any] = {
            "run_timestamp": run_timestamp,
            "extract_meta_id": meta_id,
        }
        for report_key, db_col in _ISSUE_KEY_MAP.items():
            row[db_col] = r.get(report_key, "")
        db_records.append(row)
    conn = get_connection()
    try:
        _bulk_insert(conn, "validation_results", _VALIDATION_COLS, db_records)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------


def load_table(table_name: str) -> list[dict[str, Any]]:
    """Load all rows from a table as a list of plain dicts."""
    _validate_table_name(table_name)
    conn = get_connection(read_only=True)
    cur = conn.execute(f"SELECT * FROM {table_name}")
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Extract-meta helpers
# ---------------------------------------------------------------------------


def save_extract_meta(
    country: str, positions_fetched: int, complete: bool = False
) -> int:
    conn = get_connection()
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO extract_meta (run_timestamp, country, positions_fetched, extract_complete) "
            "VALUES (?, ?, ?, ?)",
            (ts, country, positions_fetched, 1 if complete else 0),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def mark_extract_complete(meta_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE extract_meta SET extract_complete=1 WHERE id=?", (meta_id,)
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_extract_meta() -> dict[str, Any] | None:
    try:
        conn = get_connection(read_only=True)
    except FileNotFoundError:
        return None
    cur = conn.execute(
        "SELECT * FROM extract_meta WHERE extract_complete=1 ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None
