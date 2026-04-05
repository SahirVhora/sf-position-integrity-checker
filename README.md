# SF Position Integrity Checker

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)
![Platform: SAP SuccessFactors](https://img.shields.io/badge/platform-SAP%20SuccessFactors-0FAAFF)

A Python CLI tool that validates SAP SuccessFactors **Position** object data integrity by fetching positions and foundation objects via OData v2 and running cross-entity alignment checks. Built for SAP SuccessFactors consultants and HR system administrators who need to catch hierarchy, job-code, and cost-centre misalignments before they cause payroll or reporting errors.

---

## What it checks

| Check | Description | Severity |
|-------|-------------|----------|
| CHK-09 | Sub Department must belong to the Position's Department | CRITICAL |
| CHK-10 | Department must belong to the Position's Division | CRITICAL |
| CHK-11 | Division must be linked to the Position's Business Unit | CRITICAL |
| CHK-12 | Business Unit must be linked to the Position's Legal Entity | CRITICAL |
| CHK-13 | Cost Centre must be linked to the Position's Business Unit | CRITICAL |
| CHK-15 | Job Code Job Family must match Position's Job Family | HIGH |
| CHK-16 | Job Code Sub Family must match Position's Job Sub Family | HIGH |
| CHK-17 | Job Code Grade must match Position's Global Job Level | HIGH |
| CHK-18 | Job Code Career Path must match Position's Career Path | HIGH |

---

## How it works

```
SF Tenant (OData v2)
        │
        ▼
  [Fetch & Cache]  ──  Positions + Foundation Objects → SQLite (local, offline-capable)
        │
        ▼
  [Rule Engine]    ──  config/rules.yaml drives CHK-09 to CHK-18 (configurable)
        │
        ▼
  [Reports]        ──  HTML (filterable) │ Excel workbook │ Console table │ run_manifest.json
```

---

## Quickstart

1. **Clone the repository**
   ```bash
   git clone https://github.com/sahirvhora/sf-position-integrity-checker.git
   cd sf-position-integrity-checker
   ```

2. **Install dependencies**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Copy the example environment file**
   ```bash
   cp .env.example .env
   ```

4. **Fill in your SF credentials** in `.env`
   ```
   SF_ODATA_BASE_URL=https://api4.successfactors.com/odata/v2/
   SF_USERNAME=your_api_user
   SF_PASSWORD=your_password
   SF_COMPANY_ID=your_company_id
   ```

5. **Run the tool**
   ```bash
   python main.py --country CA
   ```
   Select **[1] Extract & Validate** on first run. Use **[2] Only Validate** for re-runs against cached data.

---

## Output examples

After each run, three report files are written to `./output/`:

| Output | Description |
|--------|-------------|
| **HTML report** | Interactive browser report with filter dropdowns (Severity, Check ID, Category), full-text search, column sort, and one-click export to CSV, Excel, or PDF via print. |
| **Excel workbook** | Two sheets: *Issues* (all failures with colour-coded rows) and *Summary* (run statistics + per-check breakdown with branding header). |
| **Console table** | Unicode box-drawing table printed to stdout showing per-check counts, severity, and run totals. |
| **run_manifest.json** | Machine-readable JSON summary of the run — suitable for CI pipelines or audit trails. |

---

## Customising rules

Rules are defined in `config/rules.yaml`. You can disable a check or change its severity without touching Python:

```yaml
rules:
  - id: CHK-17
    enabled: false          # disable this check entirely
    description: "Job Code Grade must match Position's Global Job Level"
    severity: HIGH          # change to CRITICAL if needed
    type: scalar_match
    position_field: jobCode
    lookup_key: job_codes
    lookup_field: grade
    compare_to_position_field: cust_GlobalJobLevel
    fire_when_lookup_field_not_null: true
```

Changes take effect immediately on the next run — no restart or reinstall needed.

---

## Customising foundation objects

`config/foundation.yaml` maps SF entity names and field names to the tool's internal model. If your SF instance uses different custom field names (e.g. `cust_Division2` instead of `cust_Division`), edit this file rather than touching Python. The `fetchers.py` module reads this configuration to know which OData entities and `$select`/`$expand` parameters to request.

---

## Security

- **Credentials never leave your machine.** The tool connects only to your SF tenant and stores data in a local SQLite file under `./data/`.
- **Three credential options** (see below) — choose the one that fits your security policy.
- **Tenant URL is masked** in all log output and `run_manifest.json` (`***masked***` replaces the subdomain).
- **`.env` and `*.db` files are in `.gitignore`** — they will not be committed accidentally.

---

## Credential setup

**Option 1 — `.env` file** (quickest, fine for local dev)
```bash
cp .env.example .env
# edit .env with your credentials
```

**Option 2 — OS keyring** (recommended for shared or CI environments)
```python
from config import store_credentials_to_keyring
store_credentials_to_keyring()
# Follow the interactive prompts — credentials are stored in your OS secret store
```

**Option 3 — Interactive prompt** (fallback)
If neither `.env` nor keyring credentials are found, the tool prompts you at runtime and offers to save to the keyring for future runs.

---

## Running tests

An offline test suite is included — no SF credentials needed:

```bash
python test_schema.py
```

Tests cover: SQLite schema structure, CHECK constraints, date normalisation, junction table population, all integrity checks (CHK-09 to CHK-18 pass + fail cases), validation result persistence, audit SQL views, and pipe-separated junction saving.

---

## Roadmap

- **OAuth2 / SAML2 Bearer token support** — replace Basic Auth for production tenants
- **Additional check types** — `not_null` rule type and custom expression checks via `rules.yaml`
- **Multi-country parallel runs** — fan out across all active countries in a single execution
- **Web UI** — lightweight Streamlit dashboard for non-technical stakeholders

---

## Author

[Sahir Vhora](https://www.linkedin.com/in/sahir-vhora-9242439/) — SAP SuccessFactors Consultant

---

## License

[Apache 2.0](LICENSE)
