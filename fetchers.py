"""
fetchers.py — Smart two-phase fetch strategy.

Phase 1 : Fetch all active Canada positions from SF.
Phase 2 : Collect unique foundation codes referenced by those positions.
Phase 3 : Fetch ONLY the referenced foundation records in batches of 50 codes
          per OData request (avoids URL-length limits on bulk global fetches).

All fetched data is saved to the local SQLite database via database.py.
"""

import datetime
import math
import re
from typing import Any, Dict, List, Optional, Set

from api_client import fetch_all


# ---------------------------------------------------------------------------
# Navigation-property normaliser
# ---------------------------------------------------------------------------

def _deferred_code(value: Any) -> Optional[str]:
    """
    SF OData v2 returns association/navigation fields as deferred link objects:
      {"__deferred": {"uri": "https://host/odata/v2/FOCompany('3120')"}}
    Extract and return the entity code ('3120') so we can compare it as a
    plain string.  Returns None if the URI cannot be parsed.
    """
    if isinstance(value, dict):
        uri = value.get("__deferred", {}).get("uri", "")
        if uri:
            m = re.search(r"\('([^']+)'\)$", uri)
            if m:
                return m.group(1)
    return None


def _normalize_record(record: Dict) -> Dict:
    """
    Walk every field in a raw SF API record and flatten navigation properties
    to plain string codes.  Handles three SF OData v2 patterns:

      1. Deferred link (field NOT expanded):
           {"__deferred": {"uri": ".../FOCompany('3120')"}}  →  None
           (deferred-only links cannot provide the code without a follow-up
           request; caller should use $expand instead)

      2. Expanded 1-to-1 inline object:
           {"externalCode": "3120", "__metadata": {...}, ...}  →  "3120"

      3. Expanded 1-to-N collection (SF OData wraps nav props as results list):
           {"results": [{"externalCode": "3120", ...}, ...]}  →  "3120"
           (takes the first element)

    Plain scalar values (str, int, None, …) are passed through unchanged.
    """
    result: Dict = {}
    for k, v in record.items():
        if isinstance(v, dict):
            if "results" in v and isinstance(v["results"], list):
                # Expanded nav prop returned as collection.
                # Store ALL codes as a pipe-separated string so many-to-many
                # relationships (e.g. BU → multiple LEs, CC → multiple BUs)
                # are fully preserved for membership checks in validators.
                items = v["results"]
                codes = [
                    r.get("externalCode") or r.get("code")
                    for r in items
                    if r.get("externalCode") or r.get("code")
                ]
                result[k] = "|".join(codes) if codes else None
            elif "externalCode" in v:
                result[k] = v.get("externalCode")
            elif "code" in v:
                result[k] = v.get("code")
            elif "__deferred" in v:
                # Cannot extract code from deferred link without a follow-up call
                result[k] = None
            else:
                result[k] = v
        else:
            result[k] = v
    return result

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DISTANT_FUTURE = datetime.date(9999, 12, 31)
_CODE_BATCH_SIZE = 50


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _parse_sf_date(raw: Any) -> Optional[datetime.date]:
    """
    Parse SF OData v2 date formats:
      - ISO "YYYY-MM-DD" / "YYYY-MM-DDThh:mm:ss"
      - Epoch-millis "/Date(1609459200000)/"          (positive, post-1970)
      - Epoch-millis "/Date(-2208988800000)/"          (negative, pre-1970 e.g. 01/01/1900)
      - Epoch-millis with tz "/Date(1609459200000+0200)/"
      - None / empty string → None

    Key fix: scan for timezone offset starting at position 1 so a leading '-'
    on negative epoch values is preserved (the old .split('-')[0] stripped it).
    """
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith("/Date("):
        inner = s[6:].split(")")[0]  # e.g. "1609459200000", "-2208988800000", "1609459200000+0200"
        # Strip timezone offset: scan from index 1 to keep any leading '-' sign
        ms_str = inner
        for i in range(1, len(inner)):
            if inner[i] in ("+", "-"):
                ms_str = inner[:i]
                break
        try:
            ts_sec = int(ms_str) / 1000
            # datetime.date.fromtimestamp can fail for dates outside the platform
            # range (roughly pre-1678 or post-2262 on some builds).  Fall back to
            # sentinels so the record is not silently excluded.
            return datetime.date.fromtimestamp(ts_sec)
        except (ValueError, OSError, OverflowError):
            # Negative overflow → very old date (treat as ancient past)
            # Positive overflow → far future (treat as distant future)
            try:
                return datetime.date.min if int(ms_str) < 0 else datetime.date.max
            except ValueError:
                return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.datetime.strptime(s[:len(fmt)], fmt).date()
        except ValueError:
            pass
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _is_active_fo(record: Dict, today: datetime.date) -> bool:
    start = _parse_sf_date(record.get("startDate"))
    end   = _parse_sf_date(record.get("endDate")) or _DISTANT_FUTURE
    return (start is not None) and start <= today <= end and record.get("status") == "A"


def _is_active_subdept(record: Dict, today: datetime.date) -> bool:
    start = _parse_sf_date(record.get("effectiveStartDate"))
    end   = _parse_sf_date(record.get("mdfSystemEffectiveEndDate")) or _DISTANT_FUTURE
    return (start is not None) and start <= today <= end and record.get("mdfSystemStatus") == "A"


def _is_active_position(record: Dict, today: datetime.date) -> bool:
    start = _parse_sf_date(record.get("effectiveStartDate"))
    end   = _parse_sf_date(record.get("effectiveEndDate")) or _DISTANT_FUTURE
    return (start is not None) and start <= today <= end and record.get("effectiveStatus") == "A"


def _build_lookup(
    records: List[Dict],
    today: datetime.date,
    is_active_fn,
    start_field: str = "startDate",
) -> Dict[str, Dict]:
    """Reduce versioned records to one active record per externalCode (latest startDate wins)."""
    lookup: Dict[str, Dict] = {}
    for rec in records:
        if not is_active_fn(rec, today):
            continue
        code = rec.get("externalCode")
        if not code:
            continue
        if code not in lookup:
            lookup[code] = rec
        else:
            existing_start = _parse_sf_date(lookup[code].get(start_field)) or datetime.date.min
            this_start     = _parse_sf_date(rec.get(start_field))           or datetime.date.min
            if this_start > existing_start:
                lookup[code] = rec
    return lookup


# ---------------------------------------------------------------------------
# Phase 1 — Fetch positions
# ---------------------------------------------------------------------------

def fetch_positions(country_code: str = "CAN") -> List[Dict]:
    """Fetch all active Canada positions from SF with full pagination."""
    print(f"\n[1/9] Fetching Positions (cust_Country='{country_code}')...")
    records = fetch_all(
        entity="Position",
        select_fields=[
            "code", "externalName_en_US", "effectiveStartDate", "effectiveEndDate",
            "effectiveStatus", "company", "businessUnit", "division", "department",
            "cust_subDepartment", "jobCode", "costCenter", "location", "cust_Country",
            "cust_JobFunction", "cust_jobSubFunction", "cust_GlobalJobLevel", "cust_CareerPath",
        ],
        filter_expr=f"cust_Country eq '{country_code}' and effectiveStatus eq 'A'",
    )
    today = datetime.date.today()
    lookup: Dict[str, Dict] = {}
    for rec in records:
        rec = _normalize_record(rec)
        if not _is_active_position(rec, today):
            continue
        code = rec.get("code")
        if not code:
            continue
        if code not in lookup:
            lookup[code] = rec
        else:
            existing = _parse_sf_date(lookup[code].get("effectiveStartDate")) or datetime.date.min
            this     = _parse_sf_date(rec.get("effectiveStartDate"))           or datetime.date.min
            if this > existing:
                lookup[code] = rec
    active = list(lookup.values())
    print(f"  -> {len(active)} active positions after effective date filtering")
    return active


# ---------------------------------------------------------------------------
# Phase 2 — Collect unique codes
# ---------------------------------------------------------------------------

def collect_unique_codes(positions: List[Dict]) -> Dict[str, Set[str]]:
    """Return a dict of sets: one set of unique referenced codes per foundation field."""
    codes: Dict[str, Set[str]] = {
        "company":            set(),
        "businessUnit":       set(),
        "division":           set(),
        "department":         set(),
        "cust_subDepartment": set(),
        "jobCode":            set(),
        "costCenter":         set(),
        "location":           set(),
    }
    for pos in positions:
        for field in codes:
            v = pos.get(field)
            if v and str(v).strip():
                codes[field].add(str(v).strip())

    print("\n[CODES] Unique foundation codes referenced by positions:")
    for field, s in codes.items():
        print(f"  {field:<24}: {len(s):>4} unique value(s)")
    return codes


# ---------------------------------------------------------------------------
# Phase 3 — Batched foundation fetches
# ---------------------------------------------------------------------------

def _fetch_by_codes(
    entity: str,
    step: str,
    codes: Set[str],
    select_fields: List[str],
    status_filter: str,
    is_active_fn,
    start_field: str = "startDate",
    expand_fields: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Fetch records for the given set of codes in batches of _CODE_BATCH_SIZE.
    Applies effective-date filtering after fetch and returns one active record
    per externalCode.
    """
    if not codes:
        print(f"\n[{step}] {entity}: no codes referenced — skipping")
        return []

    code_list    = sorted(codes)
    total_batches = math.ceil(len(code_list) / _CODE_BATCH_SIZE)
    today         = datetime.date.today()
    all_records: List[Dict] = []

    for batch_num in range(1, total_batches + 1):
        start = (batch_num - 1) * _CODE_BATCH_SIZE
        batch = code_list[start: start + _CODE_BATCH_SIZE]
        code_clause  = " or ".join(f"externalCode eq '{c}'" for c in batch)
        filter_expr  = f"({code_clause}) and {status_filter}"
        print(
            f"\n[{step}] Fetching {entity} for {len(codes)} unique codes "
            f"(batch {batch_num}/{total_batches})..."
        )
        records = fetch_all(
            entity=entity,
            select_fields=select_fields,
            filter_expr=filter_expr,
            expand_fields=expand_fields,
        )
        all_records.extend(_normalize_record(r) for r in records)

    lookup = _build_lookup(all_records, today, is_active_fn, start_field)
    result = list(lookup.values())
    print(f"  -> {len(result)} active {entity} record(s)")
    return result


# ---------------------------------------------------------------------------
# Individual foundation fetchers
# ---------------------------------------------------------------------------

def fetch_fo_company(codes: Set[str]) -> List[Dict]:
    return _fetch_by_codes(
        entity="FOCompany", step="2/9", codes=codes,
        select_fields=["externalCode", "startDate", "endDate", "status", "description", "country"],
        status_filter="status eq 'A'",
        is_active_fn=_is_active_fo,
    )


def fetch_fo_business_unit(codes: Set[str]) -> List[Dict]:
    return _fetch_by_codes(
        entity="FOBusinessUnit", step="3/9", codes=codes,
        select_fields=["externalCode", "startDate", "endDate", "status", "description"],
        expand_fields=["cust_legalEntity"],
        status_filter="status eq 'A'",
        is_active_fn=_is_active_fo,
    )


def fetch_fo_division(codes: Set[str]) -> List[Dict]:
    return _fetch_by_codes(
        entity="FODivision", step="4/9", codes=codes,
        select_fields=["externalCode", "startDate", "endDate", "status", "description"],
        expand_fields=["cust_BusinessUnit"],
        status_filter="status eq 'A'",
        is_active_fn=_is_active_fo,
    )


def fetch_fo_department(codes: Set[str]) -> List[Dict]:
    # FODepartment exposes its Division link two ways depending on record origin:
    #   - Global/legacy records: "parent" plain scalar string (no expand needed)
    #   - Country-specific records: "cust_Division" navigation property ($expand)
    # Fetch both; prefer expanded cust_Division, fall back to parent.
    records = _fetch_by_codes(
        entity="FODepartment", step="5/9", codes=codes,
        select_fields=["externalCode", "startDate", "endDate", "status", "description", "parent"],
        expand_fields=["cust_Division"],
        status_filter="status eq 'A'",
        is_active_fn=_is_active_fo,
    )
    for rec in records:
        if not rec.get("cust_Division"):
            rec["cust_Division"] = rec.pop("parent", None)
        else:
            rec.pop("parent", None)
    return records


def fetch_cust_sub_department(codes: Set[str]) -> List[Dict]:
    records = _fetch_by_codes(
        entity="cust_SubDepartment", step="6/9", codes=codes,
        select_fields=[
            "externalCode", "effectiveStartDate", "mdfSystemEffectiveEndDate",
            "mdfSystemStatus", "externalName_en_US",
        ],
        expand_fields=["cust_Department"],
        status_filter="mdfSystemStatus eq 'A'",
        is_active_fn=_is_active_subdept,
        start_field="effectiveStartDate",
    )
    # Rename SF-specific field names to standard schema names used in cust_sub_department table.
    # _is_active_subdept has already run (inside _fetch_by_codes), so renaming is safe here.
    for rec in records:
        rec["startDate"] = rec.pop("effectiveStartDate", None)
        rec["endDate"]   = rec.pop("mdfSystemEffectiveEndDate", None)
        rec["status"]    = rec.pop("mdfSystemStatus", None)
    return records


def fetch_fo_job_code(codes: Set[str]) -> List[Dict]:
    return _fetch_by_codes(
        entity="FOJobCode", step="7/9", codes=codes,
        select_fields=[
            "externalCode", "startDate", "endDate", "status", "name_en_US",
            "jobFunction", "cust_jobsubfunction", "grade", "cust_careerPath",
        ],
        status_filter="status eq 'A'",
        is_active_fn=_is_active_fo,
    )


def fetch_fo_job_class_local_can(codes: Set[str]) -> List[Dict]:
    # Uses same jobCode set — looks for a CAN-specific record per job code
    return _fetch_by_codes(
        entity="FOJobClassLocalCAN", step="7b/9", codes=codes,
        select_fields=[
            "externalCode", "startDate", "endDate", "status",
            "cust_LocalJobLevel", "country",
        ],
        status_filter="status eq 'A'",
        is_active_fn=_is_active_fo,
    )


def fetch_fo_cost_center(codes: Set[str]) -> List[Dict]:
    return _fetch_by_codes(
        entity="FOCostCenter", step="8/9", codes=codes,
        select_fields=["externalCode", "startDate", "endDate", "status", "description"],
        expand_fields=["cust_BusinessUnit"],
        status_filter="status eq 'A'",
        is_active_fn=_is_active_fo,
    )


def fetch_fo_location(codes: Set[str]) -> List[Dict]:
    return _fetch_by_codes(
        entity="FOLocation", step="9/9", codes=codes,
        select_fields=[
            "externalCode", "startDate", "endDate", "status",
            "description", 
        ],
        status_filter="status eq 'A'",
        is_active_fn=_is_active_fo,
    )


# ---------------------------------------------------------------------------
# Job Sub-Function via entity-key nav-prop calls (parallelised)
# ---------------------------------------------------------------------------

def _sf_date_to_odata_key(raw: Any) -> str:
    """Convert an SF date value to OData entity-key datetime format.
    e.g. '/Date(-2208988800000)/' → "datetime'1900-01-01T00:00:00'"
    """
    d = _parse_sf_date(raw)
    if d is None or d == datetime.date.min or d == datetime.date.max:
        return "datetime'1900-01-01T00:00:00'"
    return f"datetime'{d.isoformat()}T00:00:00'"


def _parse_jobcode_subfunction_response(data: Dict[str, Any]) -> Optional[str]:
    """Parse the OData JSON response for a job code's cust_jobsubfunction."""
    if not isinstance(data, dict):
        return None
    payload = data.get("d", data)
    if isinstance(payload, dict):
        if payload.get("externalCode"):
            return str(payload["externalCode"]).strip()
        results = payload.get("results")
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict) and first.get("externalCode"):
                return str(first["externalCode"]).strip()
    return None


def fetch_jobcode_subfunctions(job_code_records: List[Dict]) -> Dict[str, Optional[str]]:
    """
    Retrieve the cust_jobsubfunction code for each job code by directly
    accessing the entity-key navigation endpoint:

      FOJobCode(externalCode='X', startDate=datetime'YYYY-MM-DDTHH:MM:SS')
        /cust_jobsubfunction

    SF OData v2 returns cust_jobsubfunction as a deferred navigation property
    when fetched via $select, so the composite-key nav-prop call is the preferred
    path. On bad-request failures, we fall back to a safe expanded query.

    Calls are issued concurrently (up to 10 threads) to reduce wall-clock time.
    """
    import concurrent.futures
    import config
    from api_client import _get_with_retry, fetch_all

    total = len(job_code_records)
    if total == 0:
        return {}

    print(f"\n[7c/9] Fetching Job Sub Function for {total} job codes (parallel)...")
    result: Dict[str, Optional[str]] = {}

    def _fetch_one(rec: Dict) -> tuple:
        jc_code = rec.get("externalCode")
        if not jc_code:
            return (None, None)

        def _fetch_via_navprop() -> Optional[str]:
            start_key = _sf_date_to_odata_key(rec.get("startDate"))
            url = (
                f"{config.ODATA_BASE_URL}FOJobCode"
                f"(externalCode='{jc_code}',startDate={start_key})"
                f"/cust_jobsubfunction?$format=json"
            )
            data = _get_with_retry(url, f"cust_jobsubfunction/{jc_code}")
            return _parse_jobcode_subfunction_response(data)

        def _fetch_via_expand() -> Optional[str]:
            filter_expr = f"externalCode eq '{jc_code}'"
            records = fetch_all(
                entity="FOJobCode",
                select_fields=["externalCode", "cust_jobsubfunction"],
                expand_fields=["cust_jobsubfunction"],
                filter_expr=filter_expr,
            )
            if not records:
                return None
            normalized = _normalize_record(records[0])
            return normalized.get("cust_jobsubfunction")

        try:
            sub_code = _fetch_via_navprop()
            if sub_code is not None:
                return (jc_code, sub_code)
            raise RuntimeError("navprop response contained no cust_jobsubfunction")
        except Exception as exc:
            print(f"  [WARN] Nav-prop fetch failed for {jc_code}: {exc}. Trying fallback query...")
            try:
                sub_code = _fetch_via_expand()
                return (jc_code, sub_code)
            except Exception as fallback_exc:
                print(f"  [WARN] Fallback fetch failed for {jc_code}: {fallback_exc}")
                return (jc_code, None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_one, rec): rec for rec in job_code_records}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            jc_code, sub_code = future.result()
            if jc_code:
                result[jc_code] = sub_code
            done += 1
            if done % 50 == 0 or done == total:
                found = sum(1 for v in result.values() if v)
                print(f"  ... {done}/{total} processed — {found} sub functions found so far")

    found = sum(1 for v in result.values() if v)
    print(f"  -> {found}/{total} job codes have a sub function code")
    return result


# ---------------------------------------------------------------------------
# Full extract orchestration
# ---------------------------------------------------------------------------

def run_full_extract(country_code: str) -> Dict[str, int]:
    """
    Two-phase extract:
      1. Fetch all active positions for country_code.
      2. Collect unique foundation codes referenced by those positions.
      3. Fetch each foundation entity only for those codes.
      4. Save everything to SQLite.

    Returns a summary dict with record counts per entity.
    """
    from database import (
        init_db, save_positions, save_foundation, save_pipe_sep_junctions,
        get_connection, save_extract_meta, mark_extract_complete,
    )

    print("\n[DB] Initialising local database...")
    init_db()
    meta_id = save_extract_meta(country_code, 0, complete=False)

    # --- Phase 1: Positions ---
    positions = fetch_positions(country_code)
    if not positions:
        print(
            f"\n[WARN] No active positions found for country {country_code}. "
            "Verify cust_Country values in your SF instance."
        )
        return {"positions": 0}

    save_positions(positions)
    print(f"  [DB] {len(positions)} positions saved")

    conn = get_connection()
    conn.execute(
        "UPDATE extract_meta SET positions_fetched=? WHERE id=?",
        (len(positions), meta_id),
    )
    conn.commit()
    conn.close()

    # --- Phase 2: Unique codes ---
    unique_codes = collect_unique_codes(positions)

    # --- Phase 3: Foundation fetches ---
    companies    = fetch_fo_company(unique_codes["company"])
    bus          = fetch_fo_business_unit(unique_codes["businessUnit"])
    divisions    = fetch_fo_division(unique_codes["division"])
    departments  = fetch_fo_department(unique_codes["department"])
    subdepts     = fetch_cust_sub_department(unique_codes["cust_subDepartment"])
    job_codes    = fetch_fo_job_code(unique_codes["jobCode"])
    # Enrich job codes with their sub-function code via nav-prop (deferred field
    # cannot be resolved via $select alone in SF OData v2)
    jc_subfuncs  = fetch_jobcode_subfunctions(job_codes)
    for jc in job_codes:
        code = jc.get("externalCode")
        if code in jc_subfuncs:
            jc["cust_jobsubfunction"] = jc_subfuncs[code]
    job_can      = fetch_fo_job_class_local_can(unique_codes["jobCode"])
    cost_centers = fetch_fo_cost_center(unique_codes["costCenter"])
    locations    = fetch_fo_location(unique_codes["location"])

    # --- Save foundation tables to DB ---
    save_foundation("fo_company",             companies)
    save_foundation("fo_business_unit",       bus)
    save_foundation("fo_division",            divisions)
    save_foundation("fo_department",          departments)
    save_foundation("cust_sub_department",    subdepts)
    save_foundation("fo_job_code",            job_codes)
    save_foundation("fo_job_class_local_can", job_can)
    save_foundation("fo_cost_center",         cost_centers)
    save_foundation("fo_location",            locations)

    # --- Populate junction tables from pipe-separated nav-prop fields ---
    # These replace the removed cust_BusinessUnit / cust_legalEntity columns
    # in the main fo_* tables (records still carry the pipe-sep values from
    # _normalize_record, they just aren't stored in the main table anymore).
    save_pipe_sep_junctions(
        "fo_division_business_unit", "division_code", "bu_code",
        divisions, "cust_BusinessUnit",
    )
    save_pipe_sep_junctions(
        "fo_bu_legal_entity", "bu_code", "legal_entity_code",
        bus, "cust_legalEntity",
    )
    save_pipe_sep_junctions(
        "fo_cost_center_business_unit", "cost_center_code", "bu_code",
        cost_centers, "cust_BusinessUnit",
    )
    print("  [DB] Junction tables populated.")

    mark_extract_complete(meta_id)
    print("\n[DB] Extract complete — database is up to date.")

    return {
        "positions":      len(positions),
        "companies":      len(companies),
        "business_units": len(bus),
        "divisions":      len(divisions),
        "departments":    len(departments),
        "sub_departments":len(subdepts),
        "job_codes":      len(job_codes),
        "job_class_can":  len(job_can),
        "cost_centers":   len(cost_centers),
        "locations":      len(locations),
    }
