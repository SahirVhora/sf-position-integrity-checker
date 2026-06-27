"""
simulator.py - Mode 2: Pre-Change Impact Simulation

Propose a foundation change (re-parent an object or alter a field value) and
see which positions would be newly affected, fixed, or unchanged - before any
change is applied to the live tenant. Zero OData calls are made.
"""

import copy
import datetime
from typing import Any

# Maps user-facing entity_type strings to validators.py lookup dict keys.
_SCALAR_LOOKUP_MAP: dict[str, str] = {
    "departments": "departments",
    "sub_departments": "sub_departments",
    "job_codes": "job_codes",
    "companies": "companies",
    "locations": "locations",
}

# Junction-based entities: (junction_lookup_key, scalar_lookup_key)
_JUNCTION_MAP: dict[str, tuple[str, str]] = {
    "divisions": ("div_to_bus", "divisions"),
    "business_units": ("bu_to_les", "business_units"),
    "cost_centers": ("cc_to_bus", "cost_centers"),
}

_ALL_ENTITY_TYPES = set(_SCALAR_LOOKUP_MAP) | set(_JUNCTION_MAP)


def simulate_change(
    change: dict[str, Any],
    country: str = "CA",
    db_path: str | None = None,
) -> dict[str, Any]:
    """
    Simulate the impact of a proposed foundation change on position integrity.

    change schema:
      {
        "type":        "reparent" | "field_change",
        "entity_type": "departments" | "sub_departments" | "job_codes"
                     | "divisions" | "business_units" | "cost_centers"
                     | "companies" | "locations",
        "code":        "<foundation object external code>",
        "field":       "<field to change on the foundation record>",
                       (for junction entities use the linked-code field name e.g. "businessUnit")
        "old_value":   "<current value>"  (optional; used to replace a specific junction entry),
        "new_value":   "<proposed value>",
      }

    Returns:
      {
        "proposed_change": <echo of input change>,
        "total_positions": int,
        "newly_failing":   [issue dicts],   positions newly broken by this change
        "newly_passing":   [issue dicts],   positions fixed by this change
        "unchanged_failing":[issue dicts],  still failing regardless
        "net_impact":      int,             newly_failing - newly_passing (positive = worse)
        "check_breakdown": {check_id: count},
        "as_of_date":      "YYYY-MM-DD",
      }

    Raises:
      FileNotFoundError if no DB exists for the given country.
      ValueError for unsupported entity_type or change type.
    """
    import database
    import validators

    entity_type = change.get("entity_type", "")
    change_type = change.get("type", "")
    code = change.get("code", "")
    new_value = change.get("new_value")

    if not entity_type or not change_type or not code or new_value is None:
        raise ValueError("change requires: type, entity_type, code, new_value")
    if entity_type not in _ALL_ENTITY_TYPES:
        raise ValueError(
            f"Unsupported entity_type {entity_type!r}. Supported: {sorted(_ALL_ENTITY_TYPES)}"
        )
    if change_type not in ("reparent", "field_change"):
        raise ValueError(f"change type must be 'reparent' or 'field_change', got {change_type!r}")

    orig_path = database.DB_PATH
    if db_path:
        database.DB_PATH = db_path
    else:
        database.set_country(country)

    try:
        baseline_lookups = validators.build_lookups_from_db()
        positions = database.load_table("positions")
    finally:
        database.DB_PATH = orig_path

    as_of_date = datetime.date.today()

    modified_lookups = copy.deepcopy(baseline_lookups)
    _apply_change(modified_lookups, change)

    baseline_issues = validators.validate_positions(
        copy.deepcopy(positions), baseline_lookups, as_of_date
    )
    modified_issues = validators.validate_positions(
        copy.deepcopy(positions), modified_lookups, as_of_date
    )

    def _key(issue: dict) -> tuple[str, str]:
        return (issue["Position ID"], issue["Check ID"])

    baseline_keys = {_key(i) for i in baseline_issues}
    modified_keys = {_key(i) for i in modified_issues}

    newly_failing_keys = modified_keys - baseline_keys
    newly_passing_keys = baseline_keys - modified_keys

    newly_failing = [i for i in modified_issues if _key(i) in newly_failing_keys]
    newly_passing = [i for i in baseline_issues if _key(i) in newly_passing_keys]
    unchanged_failing = [i for i in baseline_issues if _key(i) in (baseline_keys & modified_keys)]

    check_breakdown: dict[str, int] = {}
    for issue in newly_failing:
        cid = issue["Check ID"]
        check_breakdown[cid] = check_breakdown.get(cid, 0) + 1

    return {
        "proposed_change": change,
        "total_positions": len(positions),
        "newly_failing": newly_failing,
        "newly_passing": newly_passing,
        "unchanged_failing": unchanged_failing,
        "net_impact": len(newly_failing) - len(newly_passing),
        "check_breakdown": check_breakdown,
        "as_of_date": as_of_date.isoformat(),
    }


def _apply_change(lookups: dict[str, Any], change: dict[str, Any]) -> None:
    """Mutate lookups in-place to reflect the proposed change."""
    entity_type = change["entity_type"]
    code = change["code"]
    field = change.get("field", "")
    new_value = change["new_value"]
    old_value = change.get("old_value")

    if entity_type in _JUNCTION_MAP:
        junction_key, scalar_key = _JUNCTION_MAP[entity_type]
        junction = lookups.get(junction_key, {})
        if code not in junction:
            return
        current_set = set(junction[code])
        if old_value and old_value in current_set:
            current_set.discard(old_value)
        current_set.add(new_value)
        junction[code] = current_set
    else:
        lookup_key = _SCALAR_LOOKUP_MAP[entity_type]
        lookup_dict = lookups.get(lookup_key, {})
        if code not in lookup_dict:
            return
        if not field:
            raise ValueError("field is required for scalar entity changes")
        record = dict(lookup_dict[code])
        record[field] = new_value
        lookup_dict[code] = record
