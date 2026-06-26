"""
main.py - CLI entry point for SF Position Integrity Checker.

Usage:
    python main.py --country CA

Interactive run-mode menu:
  [1] Extract & Validate  - Fetch from SF → save to DB → validate → report
  [2] Only Validate       - Validate using existing DB data → report
  [3] Only Extract        - Fetch from SF → save to DB (no validation)
"""

import datetime
import sys

import database
from reporters import GITHUB_URL, VERSION

# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _print_banner() -> None:
    import config

    width = 67
    border = "═" * width
    tool = f"SF Position Integrity Checker  v{VERSION}"
    url = GITHUB_URL
    auth_label = (
        "Auth: OAuth2 SAML Bearer Token" if config.AUTH_METHOD == "oauth2" else "Auth: Basic Auth"
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
    print("  SAP SuccessFactors - Position Integrity Checker")
    print("=" * 70)
    print(f"  Country        : {country}")
    print(f"  Run Date       : {run_date}")
    print(f"  SF Instance    : {config.SF_BASE_URL}")
    print("=" * 70)


def _parse_as_of_date(raw: str) -> datetime.date:
    return datetime.date.fromisoformat(raw.strip())


def _pick_as_of_date() -> datetime.date:
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)

    print("\nSelect run date (as-of date):")
    print(f"  [1] Today      ({today.isoformat()})")
    print(f"  [2] Tomorrow   ({tomorrow.isoformat()})")
    print("  [3] Custom date (YYYY-MM-DD)")
    print()

    while True:
        raw = input("Enter choice [1/2/3]: ").strip()
        if raw == "1":
            return today
        if raw == "2":
            return tomorrow
        if raw == "3":
            custom_raw = input("Enter custom date (YYYY-MM-DD): ").strip()
            try:
                return _parse_as_of_date(custom_raw)
            except ValueError:
                print("  Invalid date format. Use YYYY-MM-DD.")
                continue
        print("  Please enter 1, 2, or 3.")


def _pick_mode() -> int:
    print("\nSelect run mode:")
    print("  [1] Extract & Validate  - Fetch from SF, save to DB, then validate")
    print("  [2] Only Validate       - Run validation against existing DB data")
    print("  [3] Only Extract        - Fetch from SF and save to DB (no validation)")
    print()
    while True:
        raw = input("Enter choice [1/2/3]: ").strip()
        if raw in ("1", "2", "3"):
            return int(raw)
        print("  Please enter 1, 2, or 3.")


# ---------------------------------------------------------------------------
# Validate + report (shared between mode 1 and mode 2)
# ---------------------------------------------------------------------------


def _do_validate(country: str, as_of_date: datetime.date | None = None) -> None:
    """Load positions + lookups from DB, run validation, write all reports."""
    import config
    from database import get_latest_extract_meta, load_table, save_validation_results
    from reporters import write_all_reports
    from validators import build_lookups_from_db, validate_positions

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
    lookups = build_lookups_from_db()

    target_date = as_of_date or datetime.date.today()
    print(
        f"[INFO] {len(positions)} positions loaded. "
        f"Running validation (as-of {target_date.isoformat()})..."
    )
    issues = validate_positions(positions, lookups, as_of_date=target_date)
    print(f"[INFO] Validation complete. {len(issues)} issue(s) found.")

    from datetime import datetime as dt

    run_ts = dt.now().isoformat(timespec="seconds")
    save_validation_results(issues, run_ts, meta["id"])

    write_all_reports(
        issues,
        total_positions=len(positions),
        country=country,
        tenant_url=config.ODATA_BASE_URL,
        instance_id=config.SF_INSTANCE_ID,
        as_of_date=target_date,
    )
    print("\n[DONE] All reports written. Check the ./output/ directory.\n")


# ---------------------------------------------------------------------------
# Main run logic
# ---------------------------------------------------------------------------


def run() -> None:
    _print_banner()

    country = input("Enter country code (e.g., CAN, USA, IND, NLD): ").strip().upper()
    database.set_country(country)
    _print_header(country)
    as_of_date = _pick_as_of_date()
    print(f"  As-of Date     : {as_of_date.isoformat()}")
    mode = _pick_mode()

    # ------------------------------------------------------------------
    # Mode 1 - Extract & Validate
    # ------------------------------------------------------------------
    if mode == 1:
        from fetchers import run_full_extract

        summary = run_full_extract(country, as_of_date=as_of_date)
        if not summary.get("positions"):
            sys.exit(0)
        print(
            f"\n[INFO] Extract complete ({summary['positions']} positions). Starting validation..."
        )
        _do_validate(country, as_of_date=as_of_date)

    # ------------------------------------------------------------------
    # Mode 2 - Only Validate
    # ------------------------------------------------------------------
    elif mode == 2:
        _do_validate(country, as_of_date=as_of_date)

    # ------------------------------------------------------------------
    # Mode 3 - Only Extract
    # ------------------------------------------------------------------
    elif mode == 3:
        from fetchers import run_full_extract

        summary = run_full_extract(country, as_of_date=as_of_date)
        if not summary.get("positions"):
            sys.exit(0)
        print("\n[EXTRACT SUMMARY]")
        max_key = max(len(k) for k in summary)
        for entity, count in summary.items():
            print(f"  {entity:<{max_key}} : {count:>6} record(s)")
        print(f"\n[DONE] Data extracted and saved to ./data/sf_integrity_{country}.db\n")


def main() -> None:
    run()


if __name__ == "__main__":
    main()
