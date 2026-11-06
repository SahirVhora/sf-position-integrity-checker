"""Reject credential files and scan source without sending candidates to providers."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from detect_secrets.core import baseline
from detect_secrets.core.secrets_collection import SecretsCollection
from detect_secrets.settings import get_settings


def main() -> int:
    os.chdir(Path(__file__).resolve().parents[2])
    files = sys.argv[1:]
    if not files:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            check=True,
            capture_output=True,
        )
        files = [name.decode() for name in result.stdout.split(b"\0") if name]
    files = [name for name in files if Path(name).is_file()]
    forbidden = re.compile(
        r"(^|/)(\.env(?:\..*)?|\.secrets\.(?:json|ya?ml)|"
        r"[^/]*credentials[^/]*\.json|[^/]*\.(?:db|sqlite3?|pem|key|p12|pfx))$",
        re.IGNORECASE,
    )
    rejected = [
        name
        for name in files
        if forbidden.search(name)
        and not re.search(r"(?:example|sample|template|fixture)", Path(name).name, re.IGNORECASE)
    ]
    if rejected:
        print("Credential or local database files cannot be committed:")
        print("\n".join(rejected))
        return 1
    if not files:
        return 0
    known = baseline.load(
        baseline.load_from_file(".secrets.baseline"), filename=".secrets.baseline"
    )
    # Verification can transmit candidate credentials. Never enable it here.
    get_settings().disable_filters(
        "detect_secrets.filters.common.is_ignored_due_to_verification_policies"
    )
    scanned = SecretsCollection()
    for name in files:
        scanned.scan_file(name)
    findings = scanned - known
    for name, secret in findings:
        print(f"Potential {secret.type} at {name}:{secret.line_number}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
