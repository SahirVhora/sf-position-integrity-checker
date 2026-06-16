#!/usr/bin/env python3
"""MCP server for SF Position Integrity Checker.

Exposes position data integrity validation as MCP tools that any
MCP-compatible client (Hermes Agent, Claude Code, Cursor, etc.) can
discover and call. Follows the same pattern as sf-config-debt-radar's
MCP server.

Validation runs against the local SQLite cache written by an extract
(CLI option 1/3 or the web UI) - the MCP server never connects to a
tenant itself, so no credentials pass through the AI agent.

Transport: stdio (default) or SSE.

Usage:
    python3 mcp_server.py                               # stdio
    python3 mcp_server.py --transport sse --port 8091   # HTTP/SSE
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Ensure the project root is on sys.path so we can import project modules
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

mcp = FastMCP(
    "SF Position Integrity Checker",
    instructions=(
        "SAP SuccessFactors Position data integrity validation. "
        "Tools: sf_position_checks (list validation rules), "
        "sf_validate_positions (run checks against locally cached extract), "
        "sf_latest_findings (read the most recent findings JSON), "
        "sf_position_integrity_about (server info). "
        "Run an extract first via the CLI or web UI - the MCP server "
        "validates cached data and never connects to a tenant."
    ),
)


@mcp.tool(
    name="sf_position_checks",
    description="List the position integrity validation rules (CHK-01 to CHK-09) with category, severity, and description. No data access - reads config/rules.yaml.",
)
def position_checks_tool() -> str:
    """Return all validation rules with their configuration."""
    from validators import _ALL_RULES

    rules = [
        {
            "id": rule["id"],
            "enabled": rule.get("enabled", True),
            "category": rule["category"],
            "severity": rule["severity"],
            "description": rule.get("description", ""),
        }
        for rule in _ALL_RULES
    ]
    return json.dumps({"count": len(rules), "rules": rules}, indent=2)


@mcp.tool(
    name="sf_validate_positions",
    description=(
        "Run position integrity validation against the locally cached extract "
        "for a country and return findings in the shared sf-compass-findings/v1 "
        "schema. Requires a prior extract (CLI option 1/3 or web UI). "
        "Returns an error if no local database exists for the country."
    ),
)
def validate_positions_tool(country: str, as_of_date: str = "") -> str:
    """Validate cached position data for a country.

    Args:
        country: Country code used for the extract (e.g. CAN, GBR, IND).
        as_of_date: Optional ISO date (YYYY-MM-DD) for effective-dated checks.
                    Defaults to today.

    Returns:
        JSON string in sf-compass-findings/v1 schema, or an error object.
    """
    import database
    from reporters import build_findings_document
    from validators import build_lookups_from_db, validate_positions

    try:
        country = country.strip().upper()
        database.set_country(country)
        meta = database.get_latest_extract_meta()
        if meta is None:
            return json.dumps(
                {
                    "error": (
                        f"No local extract found for country '{country}'. "
                        "Run an extract first via the CLI (option 1 or 3) or the web UI."
                    )
                },
                indent=2,
            )
        target_date = (
            datetime.date.fromisoformat(as_of_date)
            if as_of_date
            else datetime.date.today()
        )
        positions = database.load_table("positions")
        lookups = build_lookups_from_db()
        issues = validate_positions(positions, lookups, as_of_date=target_date)
        document = build_findings_document(
            issues,
            total_positions=len(positions),
            country=country,
            as_of_date=target_date,
        )
        document["scope"]["extracted_at"] = meta.get("run_timestamp", "")
        return json.dumps(document, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:  # surface a structured error to the agent
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool(
    name="sf_latest_findings",
    description="Read the most recent position integrity findings JSON from the output directory, optionally filtered by country. Use to retrieve results of a previous run without re-validating.",
)
def latest_findings_tool(country: str = "") -> str:
    """Return the newest findings JSON file from ./output.

    Args:
        country: Optional country code filter (e.g. GBR). Blank matches any.
    """
    pattern = f"position_integrity_findings_{country.strip().upper() or '*'}_*.json"
    files = sorted(
        glob.glob(str(_project_root / "output" / pattern)),
        key=os.path.getmtime,
        reverse=True,
    )
    if not files:
        return json.dumps(
            {"error": f"No findings files match {pattern} in ./output."}, indent=2
        )
    return Path(files[0]).read_text(encoding="utf-8")


@mcp.tool(
    name="sf_position_integrity_about",
    description="Get information about this MCP server, its tools, and data policy.",
)
def about_tool() -> str:
    """Return metadata about this MCP server."""
    return json.dumps(
        {
            "name": "SF Position Integrity Checker (MCP)",
            "version": "1.0.0",
            "project": "sf-position-integrity-checker",
            "project_path": str(_project_root),
            "findings_schema": "sf-compass-findings/v1",
            "tools": [
                "sf_position_checks",
                "sf_validate_positions",
                "sf_latest_findings",
                "sf_position_integrity_about",
            ],
            "data_policy": (
                "Validates locally cached extract data only. The MCP server "
                "never connects to a tenant and no credentials pass through "
                "the agent. Findings contain position codes and org IDs, "
                "never employee personal data."
            ),
        },
        indent=2,
    )


def main() -> None:
    """Run the MCP server on stdio (default) or SSE."""
    import argparse

    parser = argparse.ArgumentParser(
        description="SF Position Integrity Checker - MCP Server"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio for AI agent integration)",
    )
    parser.add_argument(
        "--port", type=int, default=8091, help="Port for SSE transport (default: 8091)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host for SSE transport (default: 0.0.0.0)"
    )
    args = parser.parse_args()

    if args.transport == "sse":
        print(
            f"Starting SF Position Integrity Checker MCP server on http://{args.host}:{args.port}/mcp",
            file=sys.stderr,
        )
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
