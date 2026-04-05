"""
main.py — CLI entry point for SF Position Integrity Checker.

Usage:
    python main.py --country CA

Interactive run-mode menu:
  [1] Extract & Validate  — Fetch from SF → save to DB → validate → report
  [2] Only Validate       — Validate using existing DB data → report
  [3] Only Extract        — Fetch from SF → save to DB (no validation)
"""

import argparse
import datetime
import sys

from reporters import VERSION, GITHUB_URL

# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _print_banner() -> None:
    import config
    width  = 67
    border = "═" * width
    tool   = f"SF Position Integrity Checker  v{VERSION}"
    url    = GITHUB_URL
    auth_label = (
        "Auth: OAuth2 SAML Bearer Token"
        if config.AUTH_METHOD == "oauth2"
        else "Auth: Basic Auth"
    )
    print(f"\n╔{border}╗")
    print(f"║{tool.center(width)}║")
    print(f"║{auth_label.center(width)}║")
    print(f"║{url.center(width)}║")
    print(f"╚{border}╝\n")


def _print_header(country: str) -> None:
    import config
    run_date = datetime.date.today().isoformat()
    print("=" * 70)
    print("  SAP SuccessFactors — Position Integrity Checker")
    print("=" * 70)
    print(f"  Country        : {country}")
    print(f"  Run Date       : {run_date}")
    print(f"  SF Instance    : {config.SF_BASE_URL}")
    print("=" * 70)


def _pick_mode() -> int:
    print("\nSelect run mode:")
    print("  [1] Extract & Validate  — Fetch from SF, save to DB, then validate")
    print("  [2] Only Validate       — Run validation against existing DB data")
    print("  [3] Only Extract        — Fetch from SF and save to DB (no validation)")
    print()
    while True:
        raw = input("Enter choice [1/2/3]: ").strip()
        if raw in ("1", "2", "3"):
            return int(raw)
        print("  Please enter 1, 2, or 3.")


# ---------------------------------------------------------------------------
# Validate + report (shared between mode 1 and mode 2)
# ---------------------------------------------------------------------------

def _do_validate(country: str) -> None:
    """Load positions + lookups from DB, run validation, write all reports."""
    import config
    from database import load_table, get_latest_extract_meta, save_validation_results
    from validators import validate_positions, build_lookups_from_db
    from reporters import write_all_reports

    meta = get_latest_extract_meta()
    if meta is None:
        print(
            "\n[ERROR] No database found. "
            "Please run Option 1 (Extract & Validate) or Option 3 (Only Extract) first."
        )
        sys.exit(1)

    print(
        f"\n[DB] Loading data from local database "
        f"(extracted: {meta['run_timestamp']}, "
        f"{meta['positions_fetched']} positions)..."
    )
    positions = load_table("positions")
    lookups   = build_lookups_from_db()

    print(f"[INFO] {len(positions)} positions loaded. Running validation (CHK-09 to CHK-18)...")
    issues = validate_positions(positions, lookups)
    print(f"[INFO] Validation complete. {len(issues)} issue(s) found.")

    from datetime import datetime as dt
    run_ts = dt.now().isoformat(timespec="seconds")
    save_validation_results(issues, run_ts, meta["id"])

    write_all_reports(
        issues,
        total_positions=len(positions),
        country=country,
        tenant_url=config.ODATA_BASE_URL,
    )
    print("\n[DONE] All reports written. Check the ./output/ directory.\n")


# ---------------------------------------------------------------------------
# Main run logic
# ---------------------------------------------------------------------------

def run(country: str) -> None:
    _print_banner()

    if country not in ("CA", "CAN"):
        print(
            f"\n[INFO] Country '{country}' is not supported in this version.\n"
            "       Only CA / CAN (Canada) is currently implemented."
        )
        sys.exit(0)

    country = "CAN"
    _print_header(country)
    mode = _pick_mode()

    # ------------------------------------------------------------------
    # Mode 1 — Extract & Validate
    # ------------------------------------------------------------------
    if mode == 1:
        from fetchers import run_full_extract
        summary = run_full_extract(country)
        if not summary.get("positions"):
            sys.exit(0)
        print(f"\n[INFO] Extract complete ({summary['positions']} positions). Starting validation...")
        _do_validate(country)

    # ------------------------------------------------------------------
    # Mode 2 — Only Validate
    # ------------------------------------------------------------------
    elif mode == 2:
        _do_validate(country)

    # ------------------------------------------------------------------
    # Mode 3 — Only Extract
    # ------------------------------------------------------------------
    elif mode == 3:
        from fetchers import run_full_extract
        summary = run_full_extract(country)
        if not summary.get("positions"):
            sys.exit(0)
        print("\n[EXTRACT SUMMARY]")
        max_key = max(len(k) for k in summary)
        for entity, count in summary.items():
            print(f"  {entity:<{max_key}} : {count:>6} record(s)")
        print("\n[DONE] Data extracted and saved to ./data/sf_integrity_CA.db\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SF SuccessFactors Position Integrity Checker (Canada)"
    )
    parser.add_argument(
        "--country",
        default="CA",
        help="Country code to check (currently only CA / CAN is supported)",
    )
    args = parser.parse_args()
    run(country=args.country.upper())


if __name__ == "__main__":
    main()
