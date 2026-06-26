"""
db_inspector.py - Interactive SQLite inspector for sf_integrity_CA.db

Usage:
    python db_inspector.py               # interactive menu
    python db_inspector.py "<SQL>"       # run a single query directly

Sample queries are printed for each object so you can copy-paste and modify them.
"""

import os
import sqlite3
import sys

DB_PATH = os.path.join("data", "sf_integrity_CA.db")

# ---------------------------------------------------------------------------
# Sample queries per table
# ---------------------------------------------------------------------------

SAMPLES = {
    "extract_meta": {
        "desc": "Extract run history",
        "queries": [
            ("All extract runs", "SELECT * FROM extract_meta ORDER BY id DESC;"),
            (
                "Latest complete extract",
                "SELECT * FROM extract_meta WHERE extract_complete=1 ORDER BY id DESC LIMIT 1;",
            ),
        ],
    },
    "positions": {
        "desc": "Canada Positions",
        "queries": [
            ("Row count", "SELECT COUNT(*) AS total FROM positions;"),
            (
                "First 10 positions",
                "SELECT code, externalName_en_US, effectiveStartDate, company, businessUnit, "
                "division, department, cust_subDepartment, jobCode, costCenter, location "
                "FROM positions LIMIT 10;",
            ),
            (
                "Positions with a specific sub-department (edit NZL00011)",
                "SELECT code, externalName_en_US, department, cust_subDepartment "
                "FROM positions WHERE cust_subDepartment = 'NZL00011';",
            ),
            (
                "Distinct companies",
                "SELECT DISTINCT company FROM positions ORDER BY company;",
            ),
            (
                "Distinct business units",
                "SELECT DISTINCT businessUnit FROM positions ORDER BY businessUnit;",
            ),
            (
                "Positions missing GlobalJobLevel",
                "SELECT code, jobCode, cust_GlobalJobLevel FROM positions "
                "WHERE cust_GlobalJobLevel IS NULL OR cust_GlobalJobLevel = '' LIMIT 20;",
            ),
        ],
    },
    "fo_company": {
        "desc": "Legal Entities (FOCompany)",
        "queries": [
            (
                "All legal entities",
                "SELECT externalCode, description, status, startDate, endDate, country FROM fo_company;",
            ),
            ("Row count", "SELECT COUNT(*) AS total FROM fo_company;"),
        ],
    },
    "fo_business_unit": {
        "desc": "Business Units (FOBusinessUnit)",
        "queries": [
            ("Row count", "SELECT COUNT(*) AS total FROM fo_business_unit;"),
            (
                "All business units",
                "SELECT externalCode, description, status FROM fo_business_unit ORDER BY externalCode;",
            ),
            (
                "BU → Legal Entity links (junction table)",
                "SELECT bu_code, GROUP_CONCAT(legal_entity_code, '|') AS legal_entities "
                "FROM fo_bu_legal_entity GROUP BY bu_code ORDER BY bu_code;",
            ),
        ],
    },
    "fo_division": {
        "desc": "Divisions (FODivision)",
        "queries": [
            ("Row count", "SELECT COUNT(*) AS total FROM fo_division;"),
            (
                "All divisions",
                "SELECT externalCode, description, status FROM fo_division ORDER BY externalCode;",
            ),
            (
                "Division → BU links (junction table)",
                "SELECT division_code, GROUP_CONCAT(bu_code, '|') AS business_units "
                "FROM fo_division_business_unit GROUP BY division_code ORDER BY division_code;",
            ),
        ],
    },
    "fo_department": {
        "desc": "Departments (FODepartment)",
        "queries": [
            ("Row count", "SELECT COUNT(*) AS total FROM fo_department;"),
            (
                "All departments with division link",
                "SELECT externalCode, description, cust_Division FROM fo_department ORDER BY externalCode;",
            ),
            (
                "Look up a specific department (edit 10013222)",
                "SELECT * FROM fo_department WHERE externalCode IN ('10013222','10013223');",
            ),
        ],
    },
    "cust_sub_department": {
        "desc": "Sub-Departments (cust_SubDepartment)",
        "queries": [
            ("Row count", "SELECT COUNT(*) AS total FROM cust_sub_department;"),
            (
                "All sub-departments with department link",
                "SELECT externalCode, externalName_en_US, cust_Department, status, "
                "startDate, endDate FROM cust_sub_department ORDER BY externalCode;",
            ),
            (
                "Look up NZL00011 specifically",
                "SELECT * FROM cust_sub_department WHERE externalCode = 'NZL00011';",
            ),
            (
                "Sub-departments where cust_Department is null/empty",
                "SELECT externalCode, externalName_en_US FROM cust_sub_department "
                "WHERE cust_Department IS NULL OR cust_Department = '';",
            ),
        ],
    },
    "fo_job_code": {
        "desc": "Job Codes (FOJobCode)",
        "queries": [
            ("Row count", "SELECT COUNT(*) AS total FROM fo_job_code;"),
            (
                "All job codes with grade (Global Job Level)",
                "SELECT externalCode, name_en_US, jobFunction, cust_jobsubfunction, grade, cust_careerPath "
                "FROM fo_job_code ORDER BY externalCode;",
            ),
            (
                "Job codes where grade is null",
                "SELECT externalCode, name_en_US FROM fo_job_code WHERE grade IS NULL OR grade = '';",
            ),
            (
                "Look up job code 60000000",
                "SELECT * FROM fo_job_code WHERE externalCode = '60000000';",
            ),
        ],
    },
    "fo_job_class_local_can": {
        "desc": "Canada Local Job Level (FOJobClassLocalCAN)",
        "queries": [
            ("Row count", "SELECT COUNT(*) AS total FROM fo_job_class_local_can;"),
            (
                "All CAN job class records",
                "SELECT externalCode, cust_LocalJobLevel, status FROM fo_job_class_local_can ORDER BY externalCode;",
            ),
            (
                "Missing local job level",
                "SELECT externalCode FROM fo_job_class_local_can "
                "WHERE cust_LocalJobLevel IS NULL OR cust_LocalJobLevel = '';",
            ),
        ],
    },
    "fo_cost_center": {
        "desc": "Cost Centres (FOCostCenter)",
        "queries": [
            ("Row count", "SELECT COUNT(*) AS total FROM fo_cost_center;"),
            (
                "All cost centres",
                "SELECT externalCode, description, status FROM fo_cost_center ORDER BY externalCode;",
            ),
            (
                "Cost Centre → BU links (junction table)",
                "SELECT cost_center_code, GROUP_CONCAT(bu_code, '|') AS business_units "
                "FROM fo_cost_center_business_unit GROUP BY cost_center_code ORDER BY cost_center_code;",
            ),
        ],
    },
    "fo_location": {
        "desc": "Locations (FOLocation)",
        "queries": [
            ("Row count", "SELECT COUNT(*) AS total FROM fo_location;"),
            (
                "All locations",
                "SELECT externalCode, description FROM fo_location ORDER BY externalCode;",
            ),
        ],
    },
    "validation_results": {
        "desc": "Validation Results (last run)",
        "queries": [
            (
                "Summary by check",
                "SELECT check_id, severity, COUNT(*) AS cnt FROM validation_results "
                "GROUP BY check_id, severity ORDER BY check_id;",
            ),
            (
                "All CRITICAL issues",
                "SELECT position_code, position_title, check_id, issue_description "
                "FROM validation_results WHERE severity='CRITICAL' ORDER BY check_id, position_code;",
            ),
            (
                "CHK-01 issues with sub-dept/dept context (join to positions)",
                "SELECT vr.position_code, vr.position_title, "
                "p.cust_subDepartment, p.department, vr.issue_description "
                "FROM validation_results vr "
                "LEFT JOIN positions p ON vr.position_code = p.code "
                "WHERE vr.check_id='CHK-01';",
            ),
            (
                "CHK-08 issues with job code context (join to positions)",
                "SELECT vr.position_code, vr.position_title, "
                "p.jobCode, p.cust_GlobalJobLevel, vr.issue_description "
                "FROM validation_results vr "
                "LEFT JOIN positions p ON vr.position_code = p.code "
                "WHERE vr.check_id='CHK-08';",
            ),
            (
                "All issues for a specific position (edit code)",
                "SELECT check_id, check_category, issue_description, severity "
                "FROM validation_results WHERE position_code = '63109362';",
            ),
            (
                "Run history - issues per extract run",
                "SELECT em.run_timestamp, em.country, em.positions_fetched, "
                "COUNT(vr.id) AS issues_found "
                "FROM extract_meta em "
                "LEFT JOIN validation_results vr ON em.id = vr.extract_meta_id "
                "GROUP BY em.id ORDER BY em.id DESC;",
            ),
        ],
    },
}

# Cross-object diagnostic queries
DIAGNOSTICS = [
    (
        "Positions whose sub-dept is NOT in cust_sub_department table",
        "SELECT p.code, p.externalName_en_US, p.cust_subDepartment "
        "FROM positions p "
        "LEFT JOIN cust_sub_department sd ON p.cust_subDepartment = sd.externalCode "
        "WHERE p.cust_subDepartment IS NOT NULL AND p.cust_subDepartment != '' "
        "AND sd.externalCode IS NULL "
        "ORDER BY p.cust_subDepartment;",
    ),
    (
        "Sub-dept → Dept mismatches (CHK-01 cross-check)",
        "SELECT p.code, p.externalName_en_US, p.cust_subDepartment, "
        "p.department AS pos_dept, sd.cust_Department AS sd_dept "
        "FROM positions p "
        "JOIN cust_sub_department sd ON p.cust_subDepartment = sd.externalCode "
        "WHERE sd.cust_Department != p.department "
        "ORDER BY p.code;",
    ),
    (
        "Job code grade vs position GlobalJobLevel mismatch (CHK-08 cross-check)",
        "SELECT p.code, p.externalName_en_US, p.jobCode, "
        "jc.grade AS jc_grade, p.cust_GlobalJobLevel AS pos_gjl "
        "FROM positions p "
        "JOIN fo_job_code jc ON p.jobCode = jc.externalCode "
        "WHERE jc.grade IS NOT NULL AND jc.grade != '' "
        "AND (p.cust_GlobalJobLevel IS NULL OR p.cust_GlobalJobLevel != jc.grade) "
        "ORDER BY p.code;",
    ),
    (
        "BU → Legal Entity mismatches (CHK-04 cross-check, uses junction table)",
        "SELECT p.code, p.businessUnit AS pos_bu, p.company AS pos_le "
        "FROM positions p "
        "LEFT JOIN fo_bu_legal_entity ble "
        "  ON p.businessUnit = ble.bu_code AND p.company = ble.legal_entity_code "
        "WHERE ble.bu_code IS NULL "
        "  AND p.businessUnit IS NOT NULL AND p.businessUnit != '' "
        "ORDER BY p.code;",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_conn() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        print(f"\n[ERROR] Database not found at '{DB_PATH}'")
        print("  Run Option 1 (Extract & Validate) or Option 3 (Only Extract) first.\n")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def run_query(sql: str) -> None:
    conn = get_conn()
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        if not rows:
            print("  (no rows returned)")
            return
        # Column widths
        widths = [len(c) for c in cols]
        for row in rows:
            for i, v in enumerate(row):
                widths[i] = max(widths[i], len(str(v) if v is not None else "NULL"))
        # Print
        sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
        header = (
            "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols)) + " |"
        )
        print(sep)
        print(header)
        print(sep)
        for row in rows:
            line = (
                "| "
                + " | ".join(
                    str(v if v is not None else "NULL").ljust(widths[i])
                    for i, v in enumerate(row)
                )
                + " |"
            )
            print(line)
        print(sep)
        print(f"  {len(rows)} row(s)\n")
    except sqlite3.Error as e:
        print(f"  [SQL ERROR] {e}\n")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------


def print_main_menu() -> None:
    print("\n" + "=" * 62)
    print("  SF Position Integrity - DB Inspector")
    print("=" * 62)
    print("  Tables:")
    items = list(SAMPLES.items())
    for i, (tname, info) in enumerate(items, 1):
        print(f"  [{i:>2}] {tname:<30} {info['desc']}")
    print(f"  [{len(items) + 1:>2}] Cross-object diagnostics")
    print("  [ C] Custom SQL query")
    print("  [ Q] Quit")
    print("=" * 62)


def print_table_menu(tname: str, info: dict) -> None:
    print(f"\n--- {tname} - {info['desc']} ---")
    for i, (label, _sql) in enumerate(info["queries"], 1):
        print(f"  [{i}] {label}")
    print("  [C] Custom SQL on this table")
    print("  [B] Back")


def run_table_menu(tname: str, info: dict) -> None:
    while True:
        print_table_menu(tname, info)
        choice = input("Select > ").strip().upper()
        if choice == "B":
            return
        elif choice == "C":
            sql = input(f"  SQL (table={tname}): ").strip()
            if sql:
                run_query(sql)
        else:
            try:
                idx = int(choice) - 1
                label, sql = info["queries"][idx]
                print(f"\n  Query: {label}")
                print(f"  SQL  : {sql}\n")
                run_query(sql)
            except (ValueError, IndexError):
                print("  Invalid choice.")


def run_diagnostics_menu() -> None:
    while True:
        print("\n--- Cross-Object Diagnostics ---")
        for i, (label, _) in enumerate(DIAGNOSTICS, 1):
            print(f"  [{i}] {label}")
        print("  [B] Back")
        choice = input("Select > ").strip().upper()
        if choice == "B":
            return
        try:
            idx = int(choice) - 1
            label, sql = DIAGNOSTICS[idx]
            print(f"\n  Query: {label}")
            print(f"  SQL  : {sql}\n")
            run_query(sql)
        except (ValueError, IndexError):
            print("  Invalid choice.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Direct SQL mode: python db_inspector.py "SELECT ..."
    if len(sys.argv) > 1:
        sql = " ".join(sys.argv[1:])
        print(f"\n  SQL: {sql}\n")
        run_query(sql)
        return

    items = list(SAMPLES.items())
    while True:
        print_main_menu()
        choice = input("Select > ").strip().upper()
        if choice in ("Q", "EXIT", "QUIT"):
            print("Bye.\n")
            break
        elif choice == "C":
            sql = input("  SQL: ").strip()
            if sql:
                run_query(sql)
        elif choice == str(len(items) + 1):
            run_diagnostics_menu()
        else:
            try:
                idx = int(choice) - 1
                tname, info = items[idx]
                run_table_menu(tname, info)
            except (ValueError, IndexError):
                print("  Invalid choice.")


if __name__ == "__main__":
    main()
