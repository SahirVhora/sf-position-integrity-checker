from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sapsf_shared.assurance import new_assurance_document, validate_assurance_document

SEVERITY_MAP = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_position_assurance(
    issues: list[dict[str, Any]],
    total_positions: int,
    country: str,
    as_of_date: date | None,
    evidence_paths: list[str],
    *,
    run_id: str,
    tool_version: str,
) -> dict[str, Any]:
    countries = [
        item.strip()
        for item in os.getenv("SAPSF_ENGAGEMENT_COUNTRIES", country).split(",")
        if item.strip()
    ]
    modules = [
        item.strip()
        for item in os.getenv(
            "SAPSF_ENGAGEMENT_MODULES", "Employee Central,Position Management"
        ).split(",")
        if item.strip()
    ]
    document = new_assurance_document(
        engagement_id=os.getenv("SAPSF_ENGAGEMENT_ID", "local-migration"),
        engagement_name=os.getenv("SAPSF_ENGAGEMENT_NAME", "Migration Assurance"),
        client_alias=os.getenv("SAPSF_CLIENT_ALIAS", "LOCAL-REVIEW"),
        run_id=run_id,
        tool="sf-position-integrity-checker",
        tool_version=tool_version,
    )
    document["engagement"].update(
        {
            "countries": countries,
            "modules": modules,
            "stage": os.getenv("SAPSF_ENGAGEMENT_STAGE", "rehearsal"),
        }
    )
    document["run"]["scope"] = {
        "country": country,
        "as_of_date": as_of_date.isoformat() if as_of_date else "",
        "checks": sorted(
            {str(issue.get("Check ID", "")) for issue in issues if issue.get("Check ID")}
        ),
    }
    evidence_ids: list[str] = []
    for index, raw_path in enumerate(evidence_paths, start=1):
        path = Path(raw_path)
        if not path.is_file():
            continue
        evidence_id = f"E-REPORT-{index}"
        evidence_ids.append(evidence_id)
        document["evidence"].append(
            {
                "id": evidence_id,
                "type": "position_integrity_report",
                "description": "Hashed local position-integrity output",
                "classification": "confidential",
                "source": f"position-integrity-report-{index}{path.suffix.lower()}",
                "sha256": _sha256(path),
                "generated_at": datetime.now(UTC).isoformat(),
            }
        )

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for issue in issues:
        grouped[
            (
                str(issue.get("Check ID", "UNKNOWN")),
                str(issue.get("Check Category", "position_integrity")),
                str(issue.get("Failed Field", "")),
            )
        ].append(issue)
    counts: Counter[str] = Counter()
    for (check_id, category, field), group in sorted(grouped.items()):
        severity = max(
            (SEVERITY_MAP.get(str(item.get("Severity", "LOW")).upper(), "low") for item in group),
            key=("info", "low", "medium", "high", "critical").index,
        )
        counts[severity] += 1
        suffix = (
            hashlib.sha256("\x1f".join((check_id, category, field)).encode())
            .hexdigest()[:16]
            .upper()
        )
        finding_id = f"F-{suffix}"
        action_id = f"A-{suffix}"
        affected = len(
            {str(item.get("Position ID", "")) for item in group if item.get("Position ID")}
        )
        description = next(
            (
                str(item.get("Issue Description", ""))
                for item in group
                if item.get("Issue Description")
            ),
            "Position integrity check failed.",
        )
        document["findings"].append(
            {
                "id": finding_id,
                "rule_id": check_id,
                "severity": severity,
                "status": "open",
                "category": category,
                "title": f"{check_id} position integrity finding",
                "description": f"{affected} position(s) affected. {description}",
                "object_type": "Position",
                "object_ref": field or check_id,
                "evidence_refs": evidence_ids,
                "action_refs": [action_id],
            }
        )
        document["actions"].append(
            {
                "id": action_id,
                "title": f"Review and resolve {check_id}",
                "owner_role": "Position Management Lead",
                "priority": severity
                if severity in {"critical", "high", "medium", "low"}
                else "low",
                "status": "open",
                "finding_refs": [finding_id],
            }
        )
    status = (
        "blocked"
        if counts["critical"]
        else ("attention_required" if document["findings"] else "pass")
    )
    document["summary"] = {
        "status": status,
        "records_assessed": max(0, int(total_positions)),
        "findings": len(document["findings"]),
        "by_severity": dict(sorted(counts.items())),
    }
    validate_assurance_document(document)
    return document


def write_position_assurance(
    issues: list[dict[str, Any]],
    total_positions: int,
    country: str,
    as_of_date: date | None,
    evidence_paths: list[str],
    output_dir: str,
    tool_version: str,
) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"position-integrity-{country}-{stamp}"
    document = build_position_assurance(
        issues,
        total_positions,
        country,
        as_of_date,
        evidence_paths,
        run_id=run_id,
        tool_version=tool_version,
    )
    path = Path(output_dir) / f"position_integrity_assurance_{country}_{stamp}.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return str(path)
