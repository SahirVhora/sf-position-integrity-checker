"""
validators.py — Integrity checks CHK-09 to CHK-18 for positions.

Check definitions are loaded from config/rules.yaml at import time.
The rule engine supports three rule types:
  scalar_match   — look up a foundation record and compare a field
  set_membership — check membership in a junction-table set
  not_null       — field must be non-blank (not yet used but supported)

If a referenced foundation code is not in the lookup (e.g. it was inactive
and not fetched), that specific alignment check is silently skipped — no
false existence errors are raised.
"""

import os
from typing import Any, Dict, List, Optional, Set

import yaml

# ---------------------------------------------------------------------------
# Load rules from YAML
# ---------------------------------------------------------------------------

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "rules.yaml")

with open(_RULES_PATH, "r", encoding="utf-8") as _f:
    _rules_data = yaml.safe_load(_f)

_ALL_RULES: List[Dict[str, Any]] = _rules_data.get("rules", [])
_ENABLED_RULES: List[Dict[str, Any]] = [r for r in _ALL_RULES if r.get("enabled", True)]

# CHECK_META is built dynamically from the YAML so reporters can consume it
# without knowing the rule definitions themselves.
# 'field' uses position_field (the SF field being validated on the position).
def _failed_field(rule: Dict[str, Any]) -> str:
    """Return the position field that should be reported as failing."""
    if rule.get("type") == "scalar_match" and rule.get("compare_to_position_field"):
        return rule["compare_to_position_field"]
    return rule["position_field"]

CHECK_META: Dict[str, Dict[str, str]] = {
    rule["id"]: {
        "category":    rule["category"],
        "field":       _failed_field(rule),
        "severity":    rule["severity"],
        "description": rule.get("description", ""),
    }
    for rule in _ALL_RULES  # include disabled rules so the summary table is complete
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _val(record: Dict[str, Any], field: str) -> Optional[str]:
    """Return stripped string value or None if absent/blank."""
    v = record.get(field)
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _set_display(codes: Set[str]) -> str:
    """Format a set of codes as a pipe-separated string for error messages."""
    return "|".join(sorted(codes)) if codes else "(none)"


def _issue(pos: Dict[str, Any], check_id: str, description: str) -> Dict[str, Any]:
    meta = CHECK_META[check_id]
    return {
        "Position ID":          _val(pos, "code") or "",
        "Position Title":       _val(pos, "externalName_en_US") or "",
        "Effective Start Date": _val(pos, "effectiveStartDate") or "",
        "Legal Entity":         _val(pos, "company") or "",
        "Business Unit":        _val(pos, "businessUnit") or "",
        "Division":             _val(pos, "division") or "",
        "Department":           _val(pos, "department") or "",
        "Sub Department":       _val(pos, "cust_subDepartment") or "",
        "Job Code":             _val(pos, "jobCode") or "",
        "Cost Centre":          _val(pos, "costCenter") or "",
        "Location":             _val(pos, "location") or "",
        "Employee ID":          _val(pos, "__empjob_userId") or "",
        "Employee Status":      _val(pos, "__empjob_emplStatus") or "",
        "Check ID":             check_id,
        "Check Category":       meta["category"],
        "Failed Field":         meta["field"],
        "Issue Description":    description,
        "Severity":             meta["severity"],
    }


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------

def _run_scalar_match(
    pos: Dict[str, Any],
    rule: Dict[str, Any],
    lookups: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Execute a scalar_match rule against one position."""
    p_key_val = _val(pos, rule["position_field"])
    if not p_key_val:
        return None

    lookup_dict: Dict = lookups.get(rule["lookup_key"], {})
    lookup_rec = lookup_dict.get(p_key_val)
    if lookup_rec is None:
        return None  # not fetched — skip silently to avoid false positives

    lookup_field_val = _val(lookup_rec, rule["lookup_field"])
    cmp_val = _val(pos, rule["compare_to_position_field"])

    if rule.get("fire_when_lookup_field_not_null"):
        # Fire whenever the foundation record defines the field, even if position is blank
        if lookup_field_val is not None and lookup_field_val != cmp_val:
            desc = (
                f"{rule['position_field']} '{p_key_val}' has "
                f"{rule['lookup_field']} '{lookup_field_val}' but "
                f"Position has {rule['compare_to_position_field']} '{cmp_val}'"
            )
            return _issue(pos, rule["id"], desc)
    else:
        # Fire only when the position carries the compare field (avoids noise for blanks)
        if cmp_val is not None and lookup_field_val != cmp_val:
            desc = (
                f"{rule['position_field']} '{p_key_val}' has "
                f"{rule['lookup_field']} '{lookup_field_val}' but "
                f"Position has {rule['compare_to_position_field']} '{cmp_val}'"
            )
            return _issue(pos, rule["id"], desc)

    return None


def _run_set_membership(
    pos: Dict[str, Any],
    rule: Dict[str, Any],
    lookups: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Execute a set_membership rule against one position."""
    p_key_val = _val(pos, rule["position_field"])
    if not p_key_val:
        return None

    lookup_dict: Dict = lookups.get(rule["lookup_key"], {})
    if p_key_val not in lookup_dict:
        return None  # not fetched — skip silently

    junction: Dict[str, Set] = lookups.get(rule["junction_lookup_key"], {})
    allowed_set: Set = junction.get(p_key_val, set())
    cmp_val = _val(pos, rule["compare_to_position_field"])

    if cmp_val is not None and cmp_val not in allowed_set:
        desc = (
            f"{rule['position_field']} '{p_key_val}' is linked to "
            f"{rule['compare_to_position_field']}(s) '{_set_display(allowed_set)}' but "
            f"Position has {rule['compare_to_position_field']} '{cmp_val}'"
        )
        return _issue(pos, rule["id"], desc)

    return None


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------

def validate_positions(
    positions: List[Dict[str, Any]],
    lookups: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Run all enabled rules from config/rules.yaml against every position.
    Returns a flat list of issue dicts (one per failed check per position).

    Supports rule types: scalar_match, set_membership.
    Rules with enabled: false are completely skipped.
    """
    issues: List[Dict[str, Any]] = []

    # Pre-enrich each position with the current employee assignment so that
    # _issue() can include Employee ID and Employee Status in every issue row.
    empjob_lookup = lookups.get("empjob", {})
    for pos in positions:
        pos_code = _val(pos, "code") or ""
        empjob_rec = empjob_lookup.get(pos_code, {})
        pos["__empjob_userId"]     = empjob_rec.get("userId", "") if empjob_rec else ""
        pos["__empjob_emplStatus"] = empjob_rec.get("emplStatus", "") if empjob_rec else ""

    for pos in positions:
        for rule in _ENABLED_RULES:
            rule_type = rule.get("type")
            result = None

            if rule_type == "scalar_match":
                result = _run_scalar_match(pos, rule, lookups)
            elif rule_type == "set_membership":
                result = _run_set_membership(pos, rule, lookups)
            # not_null and future types: extend here

            if result is not None:
                issues.append(result)

    return issues


# ---------------------------------------------------------------------------
# Load lookups from DB (used by Only-Validate mode)
# ---------------------------------------------------------------------------

def build_lookups_from_db() -> Dict[str, Any]:
    """
    Load all foundation object tables from the local SQLite database and
    return them in the same structure expected by validate_positions().

    Junction tables are loaded as set-valued dicts (key → frozenset of codes)
    for O(1) membership testing in CHK-11, CHK-12, CHK-13.
    """
    from database import load_table

    def to_lookup(table_name: str) -> Dict[str, Dict]:
        return {r["externalCode"]: r for r in load_table(table_name)}

    def to_set_lookup(table_name: str, key_col: str, val_col: str) -> Dict[str, set]:
        result: Dict[str, set] = {}
        for row in load_table(table_name):
            k = row.get(key_col)
            v = row.get(val_col)
            if k and v:
                result.setdefault(k, set()).add(v)
        return result

    return {
        "companies":       to_lookup("fo_company"),
        "business_units":  to_lookup("fo_business_unit"),
        "divisions":       to_lookup("fo_division"),
        "departments":     to_lookup("fo_department"),
        "sub_departments": to_lookup("cust_sub_department"),
        "job_codes":       to_lookup("fo_job_code"),
        "job_class_can":   to_lookup("fo_job_class_local_can"),
        "cost_centers":    to_lookup("fo_cost_center"),
        "locations":       to_lookup("fo_location"),
        # EmpJob: current employee assignment keyed by position_code
        "empjob":          {r["position_code"]: r for r in load_table("emp_job")},
        # Set-valued junction lookups
        "div_to_bus": to_set_lookup("fo_division_business_unit", "division_code", "bu_code"),
        "bu_to_les":  to_set_lookup("fo_bu_legal_entity",        "bu_code",        "legal_entity_code"),
        "cc_to_bus":  to_set_lookup("fo_cost_center_business_unit", "cost_center_code", "bu_code"),
    }
