"""Deterministic transformation guidance derived from validation findings.

The review is intentionally rules-based. It is safe to run offline, produces
repeatable output, and keeps recommendations traceable to the underlying
check IDs. An LLM can later rephrase this content, but must not invent facts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


IMPACT_BY_CATEGORY = {
    "Hierarchy Alignment": [
        "organisation and headcount reporting",
        "workflow routing and manager accountability",
        "downstream integrations that consume organisation assignments",
    ],
    "Cost Centre Alignment": [
        "workforce cost allocation and finance reconciliation",
        "payroll or ERP replication where cost centre is consumed",
    ],
    "Job Code": [
        "job architecture, reward and career-framework reporting",
        "eligibility or analytics rules that consume job attributes",
    ],
    "Foundation Status": [
        "transactions and integrations referencing inactive foundation data",
        "auditability of the organisation structure",
    ],
}


def _unique_positions(issues: list[dict[str, Any]]) -> int:
    return len({str(i.get("Position ID", "")).strip() for i in issues if i.get("Position ID")})


def build_transformation_review(
    issues: list[dict[str, Any]], total_positions: int
) -> dict[str, Any]:
    """Return an auditable, client-safe transformation review."""
    severity_counts = Counter(str(i.get("Severity", "INFO")).upper() for i in issues)
    check_counts = Counter(str(i.get("Check ID", "UNKNOWN")) for i in issues)
    category_checks: dict[str, set[str]] = defaultdict(set)
    category_counts = Counter()
    for issue in issues:
        category = str(issue.get("Check Category", "Other")).strip() or "Other"
        category_counts[category] += 1
        category_checks[category].add(str(issue.get("Check ID", "UNKNOWN")))

    impacted_positions = _unique_positions(issues)
    rate = (impacted_positions / total_positions) if total_positions else 0.0
    if severity_counts["CRITICAL"]:
        priority = "immediate_control_review"
    elif issues:
        priority = "planned_remediation"
    else:
        priority = "monitor"

    root_cause_clusters = []
    for category, count in category_counts.most_common():
        root_cause_clusters.append(
            {
                "category": category,
                "finding_count": count,
                "check_ids": sorted(category_checks[category]),
                "potential_business_impacts": IMPACT_BY_CATEGORY.get(
                    category,
                    ["processes and reports that consume the affected position attributes"],
                ),
            }
        )

    actions = []
    if issues:
        actions = [
            {
                "sequence": 1,
                "action": "Confirm scope and ownership",
                "detail": "Validate the affected population and assign HR, HRIS and business data owners.",
                "approval_required": True,
            },
            {
                "sequence": 2,
                "action": "Resolve foundation-data root causes",
                "detail": "Correct inactive or misaligned source objects before updating dependent positions.",
                "approval_required": True,
            },
            {
                "sequence": 3,
                "action": "Dry-run position remediation",
                "detail": "Generate proposed payloads, review exceptions and retain the evidence pack.",
                "approval_required": True,
            },
            {
                "sequence": 4,
                "action": "Regression test and reconcile",
                "detail": "Re-run checks and validate payroll, integrations, workflows and reporting as applicable.",
                "approval_required": True,
            },
        ]
    else:
        actions = [
            {
                "sequence": 1,
                "action": "Continue preventative monitoring",
                "detail": "Repeat the control after material foundation-data changes and before releases.",
                "approval_required": False,
            }
        ]

    return {
        "method": "deterministic_rules_v1",
        "decision_support_only": True,
        "priority": priority,
        "impacted_positions": impacted_positions,
        "impacted_position_rate": round(rate, 4),
        "finding_counts": {
            "total": len(issues),
            "critical": severity_counts["CRITICAL"],
            "high": severity_counts["HIGH"],
        },
        "top_checks": [
            {"check_id": check_id, "count": count}
            for check_id, count in check_counts.most_common(5)
        ],
        "root_cause_clusters": root_cause_clusters,
        "recommended_actions": actions,
        "human_control": {
            "writeback_automatic": False,
            "required_reviewers": ["HR data owner", "HRIS/SF owner"],
            "conditional_reviewers": ["Payroll/Integration owner", "Finance", "Data Protection"],
        },
        "evidence_boundary": (
            "Recommendations are derived only from the supplied validation findings. "
            "Potential impacts must be confirmed against the client's process and integration design."
        ),
    }
