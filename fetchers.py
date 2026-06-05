"""
fetchers.py - Smart two-phase fetch strategy.

Phase 1 : Fetch all active Canada positions from SF.
Phase 2 : Collect unique foundation codes referenced by those positions.
Phase 3 : Fetch ONLY the referenced foundation records in batches of 50 codes
          per OData request (avoids URL-length limits on bulk global fetches).

All fetched data is saved to the local SQLite database via database.py.
"""

import datetime
import math
import re
from typing import Any, Callable, Dict, List, Optional, Set

from api_client import fetch_all

ProgressCallback = Callable[[Dict[str, Any]], None]


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


def _emit_progress(callback: Optional[ProgressCallback], event: Dict[str, Any]) -> None:
    if callback:
        try:
            callback(event)
        except Exception:
            pass


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
        inner = s[6:].split(")")[
            0
        ]  # e.g. "1609459200000", "-2208988800000", "1609459200000+0200"
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
            return datetime.datetime.strptime(s[: len(fmt)], fmt).date()
        except ValueError:
            pass
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _is_active_fo(record: Dict, today: datetime.date) -> bool:
    start = _parse_sf_date(record.get("startDate"))
    end = _parse_sf_date(record.get("endDate")) or _DISTANT_FUTURE
    return (start is not None) and start <= today <= end and record.get("status") == "A"


def _is_effective_fo(record: Dict, as_of_date: datetime.date) -> bool:
    start = _parse_sf_date(record.get("startDate"))
    end = _parse_sf_date(record.get("endDate")) or _DISTANT_FUTURE
    return (start is not None) and start <= as_of_date <= end


def _is_active_subdept(record: Dict, today: datetime.date) -> bool:
    start = _parse_sf_date(record.get("effectiveStartDate"))
    end = _parse_sf_date(record.get("mdfSystemEffectiveEndDate")) or _DISTANT_FUTURE
    return (
        (start is not None)
        and start <= today <= end
        and record.get("mdfSystemStatus") == "A"
    )


def _is_effective_subdept(record: Dict, as_of_date: datetime.date) -> bool:
    start = _parse_sf_date(record.get("effectiveStartDate"))
    end = _parse_sf_date(record.get("mdfSystemEffectiveEndDate")) or _DISTANT_FUTURE
    return (start is not None) and start <= as_of_date <= end


def _is_active_position(record: Dict, today: datetime.date) -> bool:
    start = _parse_sf_date(record.get("effectiveStartDate"))
    end = _parse_sf_date(record.get("effectiveEndDate")) or _DISTANT_FUTURE
    return (
        (start is not None)
        and start <= today <= end
        and record.get("effectiveStatus") == "A"
    )


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
            existing_start = (
                _parse_sf_date(lookup[code].get(start_field)) or datetime.date.min
            )
            this_start = _parse_sf_date(rec.get(start_field)) or datetime.date.min
            if this_start > existing_start:
                lookup[code] = rec
    return lookup


# ---------------------------------------------------------------------------
# Phase 1 - Fetch positions
# ---------------------------------------------------------------------------


def fetch_positions(
    country_code: str = "CAN",
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    """Fetch all active Canada positions from SF with full pagination."""
    _emit_progress(
        progress_callback,
        {
            "phase": "positions",
            "step": "1/9",
            "message": f"Fetching Positions (cust_Country='{country_code}')...",
            "status": "running",
            "current": 0,
            "total": None,
        },
    )
    print(f"\n[1/9] Fetching Positions (cust_Country='{country_code}')...")
    records = fetch_all(
        entity="Position",
        select_fields=[
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
        ],
        filter_expr=f"cust_Country eq '{country_code}' and effectiveStatus eq 'A'",
    )
    target_date = as_of_date or datetime.date.today()
    lookup: Dict[str, Dict] = {}
    for rec in records:
        rec = _normalize_record(rec)
        if not _is_active_position(rec, target_date):
            continue
        code = rec.get("code")
        if not code:
            continue
        if code not in lookup:
            lookup[code] = rec
        else:
            existing = (
                _parse_sf_date(lookup[code].get("effectiveStartDate"))
                or datetime.date.min
            )
            this = _parse_sf_date(rec.get("effectiveStartDate")) or datetime.date.min
            if this > existing:
                lookup[code] = rec
    active = list(lookup.values())
    print(
        f"  -> {len(active)} active positions after effective date filtering "
        f"(as-of {target_date.isoformat()})"
    )
    _emit_progress(
        progress_callback,
        {
            "phase": "positions",
            "step": "1/9",
            "message": (
                f"{len(active)} active positions after effective date filtering "
                f"(as-of {target_date.isoformat()})"
            ),
            "status": "done",
            "current": len(active),
            "total": len(active),
        },
    )
    return active


# ---------------------------------------------------------------------------
# Phase 2 - Collect unique codes
# ---------------------------------------------------------------------------


def collect_unique_codes(
    positions: List[Dict],
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Set[str]]:
    """Return a dict of sets: one set of unique referenced codes per foundation field."""
    _emit_progress(
        progress_callback,
        {
            "phase": "codes",
            "step": "2/9",
            "message": "Collecting unique foundation codes referenced by positions...",
            "status": "running",
        },
    )
    codes: Dict[str, Set[str]] = {
        "company": set(),
        "businessUnit": set(),
        "division": set(),
        "department": set(),
        "cust_subDepartment": set(),
        "jobCode": set(),
        "costCenter": set(),
        "location": set(),
    }
    for pos in positions:
        for field in codes:
            v = pos.get(field)
            if v and str(v).strip():
                codes[field].add(str(v).strip())

    print("\n[CODES] Unique foundation codes referenced by positions:")
    for field, s in codes.items():
        print(f"  {field:<24}: {len(s):>4} unique value(s)")
    _emit_progress(
        progress_callback,
        {
            "phase": "codes",
            "step": "2/9",
            "message": "Unique foundation codes collected.",
            "status": "done",
            "current": sum(len(s) for s in codes.values()),
            "total": None,
        },
    )
    return codes


# ---------------------------------------------------------------------------
# Phase 3 - Batched foundation fetches
# ---------------------------------------------------------------------------


def _fetch_by_codes(
    entity: str,
    step: str,
    codes: Set[str],
    select_fields: List[str],
    is_effective_fn,
    progress_callback: Optional[ProgressCallback] = None,
    start_field: str = "startDate",
    expand_fields: Optional[List[str]] = None,
    status_filter: Optional[str] = None,
    as_of_date: Optional[datetime.date] = None,
) -> List[Dict]:
    """
    Fetch records for the given set of codes in batches of _CODE_BATCH_SIZE.
    Applies effective-date filtering after fetch and returns one effective record
    per externalCode.
    """
    if not codes:
        print(f"\n[{step}] {entity}: no codes referenced - skipping")
        _emit_progress(
            progress_callback,
            {
                "phase": entity,
                "step": step,
                "message": f"No {entity} codes referenced - skipping.",
                "status": "skipped",
                "current": 0,
                "total": 0,
            },
        )
        return []

    code_list = sorted(codes)
    total_batches = math.ceil(len(code_list) / _CODE_BATCH_SIZE)
    target_date = as_of_date or datetime.date.today()
    all_records: List[Dict] = []

    for batch_num in range(1, total_batches + 1):
        start = (batch_num - 1) * _CODE_BATCH_SIZE
        batch = code_list[start : start + _CODE_BATCH_SIZE]
        code_clause = " or ".join(f"externalCode eq '{c}'" for c in batch)
        filter_expr = (
            f"({code_clause}) and {status_filter}"
            if status_filter
            else f"({code_clause})"
        )
        print(
            f"\n[{step}] Fetching {entity} for {len(codes)} unique codes "
            f"(batch {batch_num}/{total_batches})..."
        )
        _emit_progress(
            progress_callback,
            {
                "phase": entity,
                "step": step,
                "message": f"Fetching {entity} batch {batch_num}/{total_batches}...",
                "status": "running",
                "current": batch_num,
                "total": total_batches,
            },
        )
        records = fetch_all(
            entity=entity,
            select_fields=select_fields,
            filter_expr=filter_expr,
            expand_fields=expand_fields,
        )
        all_records.extend(_normalize_record(r) for r in records)

    lookup = _build_lookup(all_records, target_date, is_effective_fn, start_field)
    result = list(lookup.values())
    print(
        f"  -> {len(result)} effective {entity} record(s) as-of {target_date.isoformat()}"
    )
    _emit_progress(
        progress_callback,
        {
            "phase": entity,
            "step": step,
            "message": f"{len(result)} effective {entity} record(s) loaded (as-of {target_date.isoformat()}).",
            "status": "done",
            "current": len(result),
            "total": len(codes),
        },
    )
    return result


# ---------------------------------------------------------------------------
# Individual foundation fetchers
# ---------------------------------------------------------------------------


def fetch_fo_company(
    codes: Set[str],
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    try:
        return _fetch_by_codes(
            entity="FOCompany",
            step="2/9",
            codes=codes,
            select_fields=[
                "externalCode",
                "startDate",
                "endDate",
                "status",
                "description",
                "country",
            ],
            is_effective_fn=_is_effective_fo,
            as_of_date=as_of_date,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        print(f"[WARN] FOCompany fetch failed: {exc}. Continuing with empty result.")
        _emit_progress(
            progress_callback,
            {
                "phase": "FOCompany",
                "step": "2/9",
                "message": f"FOCompany fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return []


def fetch_fo_business_unit(
    codes: Set[str],
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    try:
        return _fetch_by_codes(
            entity="FOBusinessUnit",
            step="3/9",
            codes=codes,
            select_fields=[
                "externalCode",
                "startDate",
                "endDate",
                "status",
                "description",
            ],
            expand_fields=["cust_legalEntity"],
            is_effective_fn=_is_effective_fo,
            as_of_date=as_of_date,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        print(
            f"[WARN] FOBusinessUnit fetch failed: {exc}. Continuing with empty result."
        )
        _emit_progress(
            progress_callback,
            {
                "phase": "FOBusinessUnit",
                "step": "3/9",
                "message": f"FOBusinessUnit fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return []


def fetch_fo_division(
    codes: Set[str],
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    try:
        return _fetch_by_codes(
            entity="FODivision",
            step="4/9",
            codes=codes,
            select_fields=[
                "externalCode",
                "startDate",
                "endDate",
                "status",
                "description",
            ],
            expand_fields=["cust_BusinessUnit"],
            is_effective_fn=_is_effective_fo,
            as_of_date=as_of_date,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        print(f"[WARN] FODivision fetch failed: {exc}. Continuing with empty result.")
        _emit_progress(
            progress_callback,
            {
                "phase": "FODivision",
                "step": "4/9",
                "message": f"FODivision fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return []


def fetch_fo_department(
    codes: Set[str],
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    # FODepartment exposes its Division link two ways depending on record origin:
    #   - Global/legacy records: "parent" plain scalar string (no expand needed)
    #   - Country-specific records: "cust_Division" navigation property ($expand)
    # Fetch both; prefer expanded cust_Division, fall back to parent.
    try:
        records = _fetch_by_codes(
            entity="FODepartment",
            step="5/9",
            codes=codes,
            select_fields=[
                "externalCode",
                "startDate",
                "endDate",
                "status",
                "description",
                "parent",
            ],
            expand_fields=["cust_Division"],
            is_effective_fn=_is_effective_fo,
            as_of_date=as_of_date,
            progress_callback=progress_callback,
        )
        for rec in records:
            if not rec.get("cust_Division"):
                rec["cust_Division"] = rec.pop("parent", None)
            else:
                rec.pop("parent", None)
        return records
    except Exception as exc:
        print(f"[WARN] FODepartment fetch failed: {exc}. Continuing with empty result.")
        _emit_progress(
            progress_callback,
            {
                "phase": "FODepartment",
                "step": "5/9",
                "message": f"FODepartment fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return []


def fetch_cust_sub_department(
    codes: Set[str],
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    try:
        records = _fetch_by_codes(
            entity="cust_SubDepartment",
            step="6/9",
            codes=codes,
            select_fields=[
                "externalCode",
                "effectiveStartDate",
                "mdfSystemEffectiveEndDate",
                "mdfSystemStatus",
                "externalName_en_US",
            ],
            expand_fields=["cust_Department"],
            is_effective_fn=_is_effective_subdept,
            as_of_date=as_of_date,
            progress_callback=progress_callback,
            start_field="effectiveStartDate",
        )
        # Rename SF-specific field names to standard schema names used in cust_sub_department table.
        # _is_active_subdept has already run (inside _fetch_by_codes), so renaming is safe here.
        for rec in records:
            rec["startDate"] = rec.pop("effectiveStartDate", None)
            rec["endDate"] = rec.pop("mdfSystemEffectiveEndDate", None)
            rec["status"] = rec.pop("mdfSystemStatus", None)
        return records
    except Exception as exc:
        print(
            f"[WARN] cust_SubDepartment fetch failed: {exc}. Continuing with empty result."
        )
        _emit_progress(
            progress_callback,
            {
                "phase": "cust_SubDepartment",
                "step": "6/9",
                "message": f"cust_SubDepartment fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return []


def fetch_fo_job_code(
    codes: Set[str],
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    try:
        return _fetch_by_codes(
            entity="FOJobCode",
            step="7/9",
            codes=codes,
            select_fields=[
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
            is_effective_fn=_is_effective_fo,
            as_of_date=as_of_date,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        print(f"[WARN] FOJobCode fetch failed: {exc}. Continuing with empty result.")
        _emit_progress(
            progress_callback,
            {
                "phase": "FOJobCode",
                "step": "7/9",
                "message": f"FOJobCode fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return []


def _job_class_entity(country_code: str) -> str:
    country_code = (country_code or "CAN").strip().upper()
    if country_code in {"NLD", "SWE", "DNK", "NOR", "IND", "IRL", "POL", "NZL"}:
        return f"cust_JobClassification{country_code}"
    else:
        return f"FOJobClassLocal{country_code}"


def _fetch_cust_job_class(
    entity: str,
    codes: Set[str],
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    """
    Dedicated fetcher for cust_JobClassification* MDF entities (IND, NLD, SWE, etc.).

    The OData metadata for these entities (confirmed via SF Admin Center) shows:
      - 'externalCode'                     Long   - internal numeric ID, NOT the job code
      - 'JobClassification_externalCode'   String - the actual job code to filter on
      - 'JobClassification_effectiveStartDate' DateTime - version start date
      - 'cust_LocalJobLevel'               String - the field we care about
      - No status or endDate field exists on this entity

    Strategy: fetch by JobClassification_externalCode, then keep the latest
    non-future-dated record per job code.
    """
    if not codes:
        print(f"\n[7b/9] {entity}: no codes referenced - skipping")
        _emit_progress(
            progress_callback,
            {
                "phase": entity,
                "step": "7b/9",
                "message": f"No {entity} codes referenced - skipping.",
                "status": "skipped",
                "current": 0,
                "total": 0,
            },
        )
        return []

    code_list = sorted(codes)
    total_batches = math.ceil(len(code_list) / _CODE_BATCH_SIZE)
    today = datetime.date.today()
    all_records: List[Dict] = []

    for batch_num in range(1, total_batches + 1):
        start_idx = (batch_num - 1) * _CODE_BATCH_SIZE
        batch = code_list[start_idx : start_idx + _CODE_BATCH_SIZE]
        code_clause = " or ".join(
            f"JobClassification_externalCode eq '{c}'" for c in batch
        )
        print(
            f"\n[7b/9] Fetching {entity} for {len(codes)} unique codes "
            f"(batch {batch_num}/{total_batches})..."
        )
        _emit_progress(
            progress_callback,
            {
                "phase": entity,
                "step": "7b/9",
                "message": f"Fetching {entity} batch {batch_num}/{total_batches}...",
                "status": "running",
                "current": batch_num,
                "total": total_batches,
            },
        )
        records = fetch_all(
            entity=entity,
            select_fields=[
                "JobClassification_externalCode",
                "JobClassification_effectiveStartDate",
                "mdfSystemRecordStatus",
                "cust_LocalJobLevel",
            ],
            # mdfSystemRecordStatus eq 'N' = Normal (active); excludes soft-deleted records
            filter_expr=f"({code_clause}) and mdfSystemRecordStatus eq 'N'",
        )
        all_records.extend(_normalize_record(r) for r in records)

    # Keep latest non-future-dated record per job code
    lookup: Dict[str, Dict] = {}
    for rec in all_records:
        jc_code = rec.get("JobClassification_externalCode")
        if not jc_code:
            continue
        this_start = (
            _parse_sf_date(rec.get("JobClassification_effectiveStartDate"))
            or datetime.date.min
        )
        if this_start > today:
            continue  # skip future-dated versions
        if jc_code not in lookup:
            lookup[jc_code] = rec
        else:
            existing_start = (
                _parse_sf_date(
                    lookup[jc_code].get("JobClassification_effectiveStartDate")
                )
                or datetime.date.min
            )
            if this_start > existing_start:
                lookup[jc_code] = rec

    # Normalise to standard field names expected by fo_job_class_local_can DB table
    # 'externalCode' key is required - that is what save_foundation/DB table expects
    result = [
        {
            "externalCode": jc_code,
            "startDate": rec.get("JobClassification_effectiveStartDate"),
            "endDate": None,
            "status": "A",
            "cust_LocalJobLevel": rec.get("cust_LocalJobLevel"),
        }
        for jc_code, rec in lookup.items()
    ]
    print(f"  -> {len(result)} active {entity} record(s)")
    _emit_progress(
        progress_callback,
        {
            "phase": entity,
            "step": "7b/9",
            "message": f"{len(result)} {entity} record(s) loaded.",
            "status": "done",
            "current": len(result),
            "total": len(codes),
        },
    )
    return result


def fetch_fo_job_class_local(
    codes: Set[str],
    country_code: str,
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    entity = _job_class_entity(country_code)
    try:
        if entity.startswith("cust_"):
            return _fetch_cust_job_class(
                entity, codes, progress_callback=progress_callback
            )
        else:
            return _fetch_by_codes(
                entity=entity,
                step="7b/9",
                codes=codes,
                select_fields=[
                    "externalCode",
                    "startDate",
                    "endDate",
                    "status",
                    "cust_LocalJobLevel",
                    "country",
                ],
                is_effective_fn=_is_effective_fo,
                as_of_date=as_of_date,
                progress_callback=progress_callback,
            )
    except Exception as exc:
        print(f"[WARN] {entity} fetch failed: {exc}. Continuing with empty result.")
        _emit_progress(
            progress_callback,
            {
                "phase": entity,
                "step": "7b/9",
                "message": f"{entity} fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return []


def fetch_fo_job_class_local_can(
    codes: Set[str],
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    # Backwards-compatible alias for the default CAN entity.
    return fetch_fo_job_class_local(
        codes, "CAN", as_of_date=as_of_date, progress_callback=progress_callback
    )


def fetch_fo_cost_center(
    codes: Set[str],
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    try:
        return _fetch_by_codes(
            entity="FOCostCenter",
            step="8/9",
            codes=codes,
            select_fields=[
                "externalCode",
                "startDate",
                "endDate",
                "status",
                "description",
            ],
            expand_fields=["cust_BusinessUnit"],
            is_effective_fn=_is_effective_fo,
            as_of_date=as_of_date,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        print(f"[WARN] FOCostCenter fetch failed: {exc}. Continuing with empty result.")
        _emit_progress(
            progress_callback,
            {
                "phase": "FOCostCenter",
                "step": "8/9",
                "message": f"FOCostCenter fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return []


def fetch_fo_location(
    codes: Set[str],
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> List[Dict]:
    try:
        return _fetch_by_codes(
            entity="FOLocation",
            step="9/9",
            codes=codes,
            select_fields=[
                "externalCode",
                "startDate",
                "endDate",
                "status",
                "description",
            ],
            is_effective_fn=_is_effective_fo,
            as_of_date=as_of_date,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        print(f"[WARN] FOLocation fetch failed: {exc}. Continuing with empty result.")
        _emit_progress(
            progress_callback,
            {
                "phase": "FOLocation",
                "step": "9/9",
                "message": f"FOLocation fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return []


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


def fetch_jobcode_subfunctions(
    job_code_records: List[Dict],
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Optional[str]]:
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

    try:
        print(f"\n[7c/9] Fetching Job Sub Function for {total} job codes (parallel)...")
        _emit_progress(
            progress_callback,
            {
                "phase": "job_subfunction",
                "step": "7c/9",
                "message": f"Fetching Job Sub Function for {total} job codes...",
                "status": "running",
                "current": 0,
                "total": total,
            },
        )
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
                print(
                    f"  [WARN] Nav-prop fetch failed for {jc_code}: {exc}. Trying fallback query..."
                )
                try:
                    sub_code = _fetch_via_expand()
                    return (jc_code, sub_code)
                except Exception as fallback_exc:
                    print(
                        f"  [WARN] Fallback fetch failed for {jc_code}: {fallback_exc}"
                    )
                    return (jc_code, None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(_fetch_one, rec): rec for rec in job_code_records
            }
            done = 0
            for future in concurrent.futures.as_completed(futures):
                jc_code, sub_code = future.result()
                if jc_code:
                    result[jc_code] = sub_code
                done += 1
                if done % 50 == 0 or done == total:
                    found = sum(1 for v in result.values() if v)
                    print(
                        f"  ... {done}/{total} processed - {found} sub functions found so far"
                    )
                    _emit_progress(
                        progress_callback,
                        {
                            "phase": "job_subfunction",
                            "step": "7c/9",
                            "message": f"Processed {done}/{total} job codes - {found} sub functions found.",
                            "status": "running",
                            "current": done,
                            "total": total,
                        },
                    )

        found = sum(1 for v in result.values() if v)
        print(f"  -> {found}/{total} job codes have a sub function code")
        _emit_progress(
            progress_callback,
            {
                "phase": "job_subfunction",
                "step": "7c/9",
                "message": f"{found}/{total} job codes have a sub function code.",
                "status": "done",
                "current": total,
                "total": total,
            },
        )
        return result
    except Exception as exc:
        print(
            f"[WARN] job_subfunction fetch failed: {exc}. Continuing with empty result."
        )
        _emit_progress(
            progress_callback,
            {
                "phase": "job_subfunction",
                "step": "7c/9",
                "message": f"job_subfunction fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return {}


# ---------------------------------------------------------------------------
# EmpJob - current employee assignment per position
# ---------------------------------------------------------------------------


def _fetch_picklist_labels(picklist_id: str) -> Dict[str, str]:
    """
    Fetch all values and their en_US labels for the given SF picklist using
    the PickListValueV2 entity.

    EmpJob.emplStatus returns the numeric lValue (option ID, e.g. 714, 722)
    rather than the alphabetic externalCode ("A", "T").  We therefore build
    the mapping keyed by BOTH lValue (as string) and externalCode so the
    translation works regardless of which form the API returns.

    SF OData endpoint:
        PickListValueV2?$filter=PickListV2_id eq '<id>'
                        &$select=externalCode,lValue,label_en_US

    Returns a dict:  {"714": "Active", "A": "Active", "722": "Leave of Absence", ...}
    """
    try:
        records = fetch_all(
            entity="PickListValueV2",
            select_fields=["externalCode", "lValue", "label_en_US"],
            filter_expr=f"PickListV2_id eq '{picklist_id}'",
        )
        mapping: Dict[str, str] = {}
        for rec in records:
            label = str(rec.get("label_en_US") or "").strip()
            ext_code = str(rec.get("externalCode") or "").strip()
            l_value = str(rec.get("lValue") or "").strip()

            if not label:
                label = ext_code or l_value  # last-resort fallback

            # Index by lValue (what EmpJob actually returns) and by externalCode
            if l_value:
                mapping[l_value] = label
            if ext_code:
                mapping[ext_code] = label

        print(
            f"  [Picklist] '{picklist_id}': {len(records)} option(s) loaded "
            f"({len(mapping)} lookup keys)"
        )
        return mapping
    except Exception as exc:
        print(f"  [WARN] Could not fetch picklist '{picklist_id}': {exc}")
        return {}


def fetch_empjob_for_positions(
    position_codes: List[str],
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Dict]:
    """
    Fetch EmpJob records for the given set of position codes and return a
    dict mapping position_code -> {userId, emplStatus, startDate} for the
    most recent assignment (latest startDate ≤ today) per position.

    Performance: batches of _CODE_BATCH_SIZE are issued concurrently (up to
    10 threads) so large position sets run in parallel rounds instead of
    sequentially.

    Employee status: the raw picklist code (e.g. "A", "T") is translated to
    the en_US label (e.g. "Active", "Terminated (Involuntary)") by first
    fetching the PicklistOption values for the 'employee-status' picklist.
    """
    import concurrent.futures

    if not position_codes:
        print("\n[EmpJob] No positions - skipping EmpJob fetch")
        _emit_progress(
            progress_callback,
            {
                "phase": "empjob",
                "step": "empjob",
                "message": "No positions - skipping EmpJob fetch.",
                "status": "skipped",
                "current": 0,
                "total": 0,
            },
        )
        return {}

    try:
        # Step 1: fetch picklist labels for employee-status so we can translate codes
        print("\n[EmpJob] Fetching employee-status picklist labels...")
        status_labels: Dict[str, str] = _fetch_picklist_labels("employee-status")

        today = datetime.date.today()
        code_list = sorted(set(position_codes))
        total_batches = math.ceil(len(code_list) / _CODE_BATCH_SIZE)

        def _fetch_batch(batch: List[str]) -> List[Dict]:
            code_clause = " or ".join(f"position eq '{c}'" for c in batch)
            raw_records = fetch_all(
                entity="EmpJob",
                select_fields=[
                    "userId",
                    "position",
                    "emplStatus",
                    "startDate",
                    "emplStatusNav",
                ],
                filter_expr=code_clause,
                expand_fields=["emplStatusNav"],
            )
            result = []
            for raw in raw_records:
                # _normalize_record converts the expanded emplStatusNav object
                # (e.g. {"externalCode": "A", ...}) into the plain externalCode string "A".
                # We then use that alphabetic code to look up the en_US label from
                # PickListValueV2 (e.g. "A" → "Active").
                rec = _normalize_record(raw)
                alpha_code = str(
                    rec.get("emplStatusNav") or ""
                ).strip()  # "A", "T", etc.
                rec["emplStatus"] = (
                    status_labels.get(alpha_code, alpha_code) if alpha_code else ""
                )
                result.append(rec)
            return result

        all_records: List[Dict] = []
        print(
            f"\n[EmpJob] Fetching EmpJob assignments for {len(code_list)} positions "
            f"({total_batches} batches, parallel)..."
        )
        _emit_progress(
            progress_callback,
            {
                "phase": "empjob",
                "step": "empjob",
                "message": f"Fetching EmpJob for {len(code_list)} positions ({total_batches} batches)...",
                "status": "running",
                "current": 0,
                "total": total_batches,
            },
        )

        batches = [
            code_list[i * _CODE_BATCH_SIZE : (i + 1) * _CODE_BATCH_SIZE]
            for i in range(total_batches)
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_num = {
                executor.submit(_fetch_batch, b): n for n, b in enumerate(batches, 1)
            }
            done = 0
            for future in concurrent.futures.as_completed(future_to_num):
                all_records.extend(future.result())
                done += 1
                if done % 10 == 0 or done == total_batches:
                    print(f"  ... {done}/{total_batches} EmpJob batches complete")
                    _emit_progress(
                        progress_callback,
                        {
                            "phase": "empjob",
                            "step": "empjob",
                            "message": f"EmpJob: {done}/{total_batches} batches complete...",
                            "status": "running",
                            "current": done,
                            "total": total_batches,
                        },
                    )

        # For each position keep the record with the most recent startDate ≤ today
        lookup: Dict[str, Dict] = {}
        for rec in all_records:
            pos_code = rec.get("position")
            if not pos_code:
                continue
            rec_start = _parse_sf_date(rec.get("startDate")) or datetime.date.min
            if rec_start > today:
                continue  # skip future-dated records
            if pos_code not in lookup:
                lookup[pos_code] = rec
            else:
                existing_start = (
                    _parse_sf_date(lookup[pos_code].get("startDate"))
                    or datetime.date.min
                )
                if rec_start > existing_start:
                    lookup[pos_code] = rec

        found = len(lookup)
        print(f"  -> {found} position(s) have an EmpJob assignment")
        _emit_progress(
            progress_callback,
            {
                "phase": "empjob",
                "step": "empjob",
                "message": f"{found}/{len(code_list)} positions have an EmpJob assignment.",
                "status": "done",
                "current": found,
                "total": len(code_list),
            },
        )
        return lookup
    except Exception as exc:
        print(f"[WARN] empjob fetch failed: {exc}. Continuing with empty result.")
        _emit_progress(
            progress_callback,
            {
                "phase": "empjob",
                "step": "empjob",
                "message": f"empjob fetch failed: {exc}. Continuing with empty result.",
                "status": "skipped",
            },
        )
        return {}


# ---------------------------------------------------------------------------
# Full extract orchestration
# ---------------------------------------------------------------------------


def run_full_extract(
    country_code: str,
    as_of_date: Optional[datetime.date] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, int]:
    """
    Two-phase extract:
      1. Fetch all active positions for country_code.
      2. Collect unique foundation codes referenced by those positions.
      3. Fetch each foundation entity only for those codes.
      4. Save everything to SQLite.

    Returns a summary dict with record counts per entity.
    """
    from database import (
        init_db,
        save_positions,
        save_foundation,
        save_pipe_sep_junctions,
        get_connection,
        save_extract_meta,
        mark_extract_complete,
    )

    _emit_progress(
        progress_callback,
        {
            "phase": "db",
            "step": "db-init",
            "message": "Initializing local database...",
            "status": "running",
        },
    )
    print("\n[DB] Initialising local database...")
    init_db()
    meta_id = save_extract_meta(country_code, 0, complete=False)

    # --- Phase 1: Positions ---
    target_date = as_of_date or datetime.date.today()
    positions = fetch_positions(
        country_code, as_of_date=target_date, progress_callback=progress_callback
    )
    if not positions:
        print(
            f"\n[WARN] No active positions found for country {country_code} "
            f"as-of {target_date.isoformat()}. "
            "Verify cust_Country values in your SF instance."
        )
        return {"positions": 0}

    save_positions(positions)
    print(f"  [DB] {len(positions)} positions saved")
    _emit_progress(
        progress_callback,
        {
            "phase": "db",
            "step": "db-save-positions",
            "message": f"Saved {len(positions)} positions to the local database.",
            "status": "running",
            "current": len(positions),
            "total": len(positions),
        },
    )

    conn = get_connection()
    conn.execute(
        "UPDATE extract_meta SET positions_fetched=? WHERE id=?",
        (len(positions), meta_id),
    )
    conn.commit()
    conn.close()

    # --- Phase 2: Unique codes ---
    unique_codes = collect_unique_codes(positions, progress_callback=progress_callback)

    # --- Phase 3: Foundation fetches ---
    companies = fetch_fo_company(
        unique_codes["company"],
        as_of_date=target_date,
        progress_callback=progress_callback,
    )
    if not companies and unique_codes["company"]:
        n = len(unique_codes["company"])
        print(
            f"[WARN] companies returned 0 records for {n} referenced codes - checks that depend on this entity will be skipped."
        )

    bus = fetch_fo_business_unit(
        unique_codes["businessUnit"],
        as_of_date=target_date,
        progress_callback=progress_callback,
    )
    if not bus and unique_codes["businessUnit"]:
        n = len(unique_codes["businessUnit"])
        print(
            f"[WARN] business_units returned 0 records for {n} referenced codes - checks that depend on this entity will be skipped."
        )

    divisions = fetch_fo_division(
        unique_codes["division"],
        as_of_date=target_date,
        progress_callback=progress_callback,
    )
    if not divisions and unique_codes["division"]:
        n = len(unique_codes["division"])
        print(
            f"[WARN] divisions returned 0 records for {n} referenced codes - checks that depend on this entity will be skipped."
        )

    departments = fetch_fo_department(
        unique_codes["department"],
        as_of_date=target_date,
        progress_callback=progress_callback,
    )
    if not departments and unique_codes["department"]:
        n = len(unique_codes["department"])
        print(
            f"[WARN] departments returned 0 records for {n} referenced codes - checks that depend on this entity will be skipped."
        )

    subdepts = fetch_cust_sub_department(
        unique_codes["cust_subDepartment"],
        as_of_date=target_date,
        progress_callback=progress_callback,
    )
    if not subdepts and unique_codes["cust_subDepartment"]:
        n = len(unique_codes["cust_subDepartment"])
        print(
            f"[WARN] sub_departments returned 0 records for {n} referenced codes - checks that depend on this entity will be skipped."
        )

    job_codes = fetch_fo_job_code(
        unique_codes["jobCode"],
        as_of_date=target_date,
        progress_callback=progress_callback,
    )
    if not job_codes and unique_codes["jobCode"]:
        n = len(unique_codes["jobCode"])
        print(
            f"[WARN] job_codes returned 0 records for {n} referenced codes - checks that depend on this entity will be skipped."
        )

    # 7b: Fetch local job classification (cust_* or FOJobClassLocal*) before subfunctions
    job_can = fetch_fo_job_class_local(
        unique_codes["jobCode"],
        country_code,
        as_of_date=target_date,
        progress_callback=progress_callback,
    )
    if not job_can and unique_codes["jobCode"]:
        n = len(unique_codes["jobCode"])
        print(
            f"[WARN] job_class_local returned 0 records for {n} referenced codes - checks that depend on this entity will be skipped."
        )

    # 7c: Enrich job codes with their sub-function code via nav-prop (deferred field
    # cannot be resolved via $select alone in SF OData v2)
    jc_subfuncs = fetch_jobcode_subfunctions(
        job_codes, progress_callback=progress_callback
    )
    if not jc_subfuncs and job_codes:
        print(
            f"[WARN] job_subfunctions returned 0 records for {len(job_codes)} job codes - checks that depend on this entity will be skipped."
        )
    for jc in job_codes:
        code = jc.get("externalCode")
        if code in jc_subfuncs:
            jc["cust_jobsubfunction"] = jc_subfuncs[code]

    cost_centers = fetch_fo_cost_center(
        unique_codes["costCenter"],
        as_of_date=target_date,
        progress_callback=progress_callback,
    )
    if not cost_centers and unique_codes["costCenter"]:
        n = len(unique_codes["costCenter"])
        print(
            f"[WARN] cost_centers returned 0 records for {n} referenced codes - checks that depend on this entity will be skipped."
        )

    locations = fetch_fo_location(
        unique_codes["location"],
        as_of_date=target_date,
        progress_callback=progress_callback,
    )
    if not locations and unique_codes["location"]:
        n = len(unique_codes["location"])
        print(
            f"[WARN] locations returned 0 records for {n} referenced codes - checks that depend on this entity will be skipped."
        )

    # --- EmpJob: current employee assignment per position ---
    pos_codes = [p["code"] for p in positions if p.get("code")]
    empjob_map = fetch_empjob_for_positions(
        pos_codes, progress_callback=progress_callback
    )
    if not empjob_map and pos_codes:
        print(
            f"[WARN] empjob returned 0 records for {len(pos_codes)} positions - employee data will be absent from reports."
        )
    empjob_rows = [
        {
            "position_code": pos_code,
            "userId": rec.get("userId", ""),
            "emplStatus": rec.get("emplStatus", ""),
            "startDate": rec.get("startDate", ""),
        }
        for pos_code, rec in empjob_map.items()
    ]

    # --- Save foundation tables to DB ---
    save_foundation("fo_company", companies)
    save_foundation("fo_business_unit", bus)
    save_foundation("fo_division", divisions)
    save_foundation("fo_department", departments)
    save_foundation("cust_sub_department", subdepts)
    save_foundation("fo_job_code", job_codes)
    save_foundation("fo_job_class_local_can", job_can)
    save_foundation("fo_cost_center", cost_centers)
    save_foundation("fo_location", locations)
    save_foundation("emp_job", empjob_rows)

    # --- Populate junction tables from pipe-separated nav-prop fields ---
    # These replace the removed cust_BusinessUnit / cust_legalEntity columns
    # in the main fo_* tables (records still carry the pipe-sep values from
    # _normalize_record, they just aren't stored in the main table anymore).
    save_pipe_sep_junctions(
        "fo_division_business_unit",
        "division_code",
        "bu_code",
        divisions,
        "cust_BusinessUnit",
    )
    save_pipe_sep_junctions(
        "fo_bu_legal_entity",
        "bu_code",
        "legal_entity_code",
        bus,
        "cust_legalEntity",
    )
    save_pipe_sep_junctions(
        "fo_cost_center_business_unit",
        "cost_center_code",
        "bu_code",
        cost_centers,
        "cust_BusinessUnit",
    )
    print("  [DB] Junction tables populated.")

    mark_extract_complete(meta_id)
    print("\n[DB] Extract complete - database is up to date.")

    return {
        "positions": len(positions),
        "companies": len(companies),
        "business_units": len(bus),
        "divisions": len(divisions),
        "departments": len(departments),
        "sub_departments": len(subdepts),
        "job_codes": len(job_codes),
        "job_class_can": len(job_can),
        "cost_centers": len(cost_centers),
        "locations": len(locations),
        "empjob": len(empjob_rows),
    }
