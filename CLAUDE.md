# CLAUDE.md — sf-position-integrity-checker

> Read this file before touching any code in this repo.
> It encodes patterns that must be followed in every change, addition, or fix.

---

## What this tool does

CLI tool that validates SAP SuccessFactors Employee Central position data
integrity by calling OData v2 APIs, running configurable checks, and producing
HTML + Excel reports. Part of the SF Compass suite at sahirvhora.github.io/sf-compass.

Target user: SF EC consultant running pre-go-live or post-migration data audits.
Not a dev tool — output must be readable by a non-technical HRIS analyst.

---

## Repo structure

```
sf-position-integrity-checker/
├── CLAUDE.md               ← this file
├── README.md
├── .env.example            ← shows required env vars, never commit real values
├── .gitignore
├── auth/                   ← auth module: Basic, OAuth2, mTLS
├── config/                 ← YAML rules files (one per check domain)
├── docs/                   ← user-facing documentation
├── templates/              ← HTML report template(s)
├── checker.py              ← main CLI entrypoint (argparse)
├── library.py              ← core check logic, OData calls, report generation
├── requirements.txt        ← pinned dependencies
└── tests/                  ← pytest tests (fixtures in tests/fixtures/)
```

If a file doesn't exist yet that belongs in this structure, create it in the
right place — don't put auth logic in checker.py or OData calls in templates.

---

## CHK Framework — the core pattern

Every check follows this exact structure. Do not deviate.

### Check identifier format
`CHK-NN` where NN is zero-padded two digits: CHK-01, CHK-02 ... CHK-11, CHK-12.
New checks continue the sequence. Never reuse an identifier.

### Severity values
Four values only, exact case: `PASS` / `WARN` / `FAIL` / `ERROR`

- `PASS` — check ran, no issues found
- `WARN` — check ran, issue found but not blocking (data quality concern)
- `FAIL` — check ran, issue found that must be fixed (data integrity breach)
- `ERROR` — check could not run (API error, missing field, config problem)

Never invent new severities. Never use "INFO", "CRITICAL", "OK", "SKIP".

### Check result object structure
```python
{
    "check_id": "CHK-07",
    "check_name": "Position to Job Classification Link",
    "severity": "FAIL",          # PASS | WARN | FAIL | ERROR
    "record_count": 142,         # total records evaluated
    "issue_count": 3,            # records with issues (0 if PASS)
    "issues": [                  # list of dicts, empty list if PASS
        {
            "position_code": "POS-001",
            "position_name": "Senior Engineer",
            "field": "jobClassification",
            "value": None,
            "message": "Position has no job classification assigned"
        }
    ],
    "message": "3 of 142 positions missing job classification"
}
```

### Rules are YAML, not hardcoded
Check thresholds, field names, and business rules live in `config/`.
Never hardcode a rule directly in Python. Example:

```yaml
# config/position_checks.yaml
CHK-07:
  name: "Position to Job Classification Link"
  enabled: true
  severity_on_fail: FAIL
  required_field: jobClassification
  null_counts_as_fail: true
```

The Python code reads config at runtime, not import time.

---

## OData v2 patterns

### Endpoint base
`https://{datacenter}.successfactors.com/odata/v2/`

Datacenter comes from config/env, never hardcoded.

### Request rules
- Always use `$select` — never fetch all fields
- Always paginate: `$top=1000&$skip={offset}` in a loop until result < $top
- Always include `$format=json` on every request
- Effective-dated entities: always filter by `effectiveStartDate le datetime'{today}'`
  and `effectiveStatus eq 'A'` unless the check explicitly requires inactive records
- User-agent header: `SF-Position-Integrity-Checker/1.0`

### Retry/backoff — mandatory on every API call
```python
import time

def odata_get(url, session, max_retries=3):
    for attempt in range(max_retries):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
```

Never make a bare `requests.get()` without retry logic.

### Pagination loop pattern
```python
def fetch_all(entity, params, session):
    results = []
    skip = 0
    while True:
        params['$skip'] = skip
        data = odata_get(build_url(entity, params), session)
        batch = data.get('d', {}).get('results', [])
        results.extend(batch)
        if len(batch) < 1000:
            break
        skip += 1000
    return results
```

---

## Auth module — `auth/`

Three modes, selected from config/env. Never prompt for credentials at runtime.

| Mode    | Config key       | How it works                                      |
|---------|------------------|---------------------------------------------------|
| Basic   | `auth: basic`    | Base64 username:password in Authorization header  |
| OAuth2  | `auth: oauth2`   | POST to `/oauth/token`, cache token, refresh 5min before expiry |
| mTLS    | `auth: mtls`     | Client cert + key paths from config, requests session |

Auth module exposes a single function: `get_session(config) -> requests.Session`
The rest of the code never touches auth directly — it only calls `get_session()`.

Credentials come from `.env` file (python-dotenv) or environment variables.
Never read credentials from config YAML — YAML gets committed, .env does not.

---

## Report output

Two outputs always generated together, never separately:

### HTML report (`templates/report.html`)
- Self-contained single file — no external CSS/JS dependencies
- Dark header, white body, red/amber/green CHK result badges
- Summary table at top: total checks, PASS/WARN/FAIL/ERROR counts
- Expandable detail section per check (vanilla JS `<details>` tag, no frameworks)
- Readable by a non-technical HRIS analyst without any explanation

### JSONL audit log
One JSON object per line. Each line = one check result dict (structure above).
File name: `sf_position_audit_{YYYYMMDD_HHMMSS}.jsonl`
This is the machine-readable output consumed by SF Compass Findings Viewer.

---

## Allowed dependencies

```
requests          # HTTP client
pyyaml            # config parsing
openpyxl          # Excel report
python-dotenv     # env var loading
flask             # only if adding web UI endpoint
jinja2            # only for HTML templating
pytest            # tests only
responses         # test mocking only
```

**Never add:**
- `pandas` — too heavy for CLI distribution
- `numpy` — not needed
- Any JS framework or build tool
- Any database (no SQLite unless specifically adding persistence feature)
- Any LLM/AI library

If a new dependency seems necessary, question whether it can be done with stdlib first.

---

## CLI interface (`checker.py`)

```bash
python checker.py --config config/checks.yaml --tenant <tenant_id> \
                  --output ./reports --checks CHK-01,CHK-07 --dry-run
```

Flags:
- `--config` — path to YAML rules file (default: config/checks.yaml)
- `--tenant` — SF tenant ID (used to name output files)
- `--output` — directory for HTML + JSONL output (default: ./reports)
- `--checks` — comma-separated CHK IDs to run (default: all enabled)
- `--dry-run` — validate config and auth only, no API calls
- `--verbose` — print each OData URL being called

Exit codes: 0 = all PASS, 1 = any FAIL, 2 = any ERROR, 3 = config/auth error.
CLI must be usable without reading the README.

---

## Test standards

Location: `tests/`
Framework: `pytest`
HTTP mocking: `responses` library (never make real API calls in tests)

### Fixture convention
```
tests/
├── fixtures/
│   ├── sample_positions_clean.json      # OData response, no issues
│   ├── sample_positions_dirty.json      # OData response, with issues
│   └── sample_config.yaml              # minimal valid config
├── test_chk01.py ... test_chk11.py     # one file per CHK
└── test_auth.py
```

### Test coverage targets
- Every CHK function: PASS case + FAIL case + WARN case (where applicable)
- Auth: Basic, OAuth2 token fetch, token refresh, mTLS session creation
- Pagination: verify loop handles exactly 1000 results and continues
- Retry: verify 429 triggers backoff and retry
- Report: verify HTML output contains correct CHK ID and severity badge
- CLI: `--dry-run` exits 0 with valid config, exits 3 with bad config

Run tests: `pytest tests/ -v --tb=short`

---

## What NOT to do

- Do not put business logic in `checker.py` (entrypoint only, delegates to library)
- Do not hardcode tenant URLs, field names, or thresholds in Python
- Do not use `print()` for output — use `logging` with configurable level
- Do not swallow exceptions — every `except` must either raise, log + raise, or return `ERROR` result
- Do not generate Excel without the matching HTML (they are always a pair)
- Do not add interactive prompts — tool must be scriptable/non-interactive
- Do not commit `.env`, `*.jsonl` reports, or `reports/` directory

---

## SF Compass suite relationship

This tool is part of a 10-tool suite. The JSONL output from this tool
feeds into the SF Compass Findings Viewer at:
`https://sahirvhora.github.io/sf-compass/findings.html`

The JSONL schema above is the contract with that viewer. Do not change it
without updating the viewer. The `check_id`, `severity`, `issue_count`,
and `issues` fields are all consumed by the viewer.

When adding a new check, also update:
1. `config/checks.yaml` — add the CHK entry
2. `README.md` — add to the checks table
3. `docs/checks_reference.md` — add full description
4. The SF Compass hub page if it lists individual checks

---

## Common tasks — how to do them correctly

### Add a new check (e.g. CHK-12)
1. Add `CHK-12` block to `config/checks.yaml`
2. Add `check_chk12(positions, config)` function in `library.py`
   — must return the standard result dict
3. Register it in the check dispatcher in `library.py`
4. Add `tests/test_chk12.py` with PASS + FAIL fixtures
5. Update README checks table

### Add a new auth method
1. Add handler in `auth/` as a new file (e.g. `auth/saml.py`)
2. Expose via `get_session()` in `auth/__init__.py`
3. Add `auth: saml` to `.env.example` and config docs
4. Add tests in `tests/test_auth.py`

### Fix a bug in an OData call
1. First write a failing test that reproduces the bug
2. Fix the call in `library.py`
3. Confirm test passes
4. Check retry and pagination still work after the fix
