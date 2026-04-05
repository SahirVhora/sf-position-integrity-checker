# SF Position Integrity Checker

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)
![Platform: SAP SuccessFactors](https://img.shields.io/badge/platform-SAP%20SuccessFactors-0FAAFF)

A Python CLI tool that validates SAP SuccessFactors **Position** object data integrity by fetching positions and foundation objects via OData v2 and running cross-entity alignment checks. Built for SAP SuccessFactors consultants and HR system administrators who need to catch hierarchy, job-code, and cost-centre misalignments before they cause payroll or reporting errors.

---

## The Problem This Solves

### After Go-Live, Foundation Data Keeps Changing — Positions Don't Self-Correct

In any live SAP SuccessFactors environment, your Foundation Objects (Departments, Divisions, Business Units, Cost Centres, Job Codes) are never static. HR Ops teams move Sub Departments between Departments, Finance deactivates Cost Centres, Job Architects change grade or career path mappings.

When these changes happen, **existing Position records are not automatically updated**. The result is silent data integrity drift — positions that reference stale or misaligned foundation values, invisible until a payroll run, headcount report, or audit surfaces the problem.

This tool solves two distinct problems:

---

### 🔍 Mode 1 — Ongoing Integrity Validation

> *"Are my positions currently clean against today's foundation data?"*

Run this after any major foundation change, as part of a periodic data health check, or ahead of a country go-live. The tool fetches live position and foundation data, runs cross-entity alignment checks, and produces a prioritised issue report.

**Typical use cases:**
- Pre go-live data quality gate
- Monthly position data health check
- Post-migration validation
- Ahead of any major foundation restructure

---

### 🔄 Mode 2 — Pre-Change Impact Analysis *(Coming Soon)*

> *"If I change this foundation object, how many positions will break — and which ones?"*

Before making a foundation change, know exactly what downstream impact it will have. The tool will let you simulate a proposed change and surface every affected Position and Job Information record — giving your team a remediation list before the change is applied, not after.

**Real-world scenarios this will handle:**

| Foundation Change | Impact Question |
|---|---|
| Sub Department moved from Dept A → Dept B | Which positions still reference the old Department alignment? |
| Cost Centre deactivated | How many positions reference this Cost Centre and need reassignment? |
| Job Code grade changed G5 → G6 | Which positions carry the old grade and need Job Info correction? |
| Job Code Career Path updated | How many positions have a mismatched Career Path after the change? |
| Division relinked to a different Business Unit | Which positions will fail the Division → BU alignment check? |

This replaces what is currently a manual process — running multiple SF reports, cross-referencing in Excel, and hoping nothing was missed — with a single command that produces a structured impact report.

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
  [Phase 1: Positions]   ──  Fetch ALL active positions for the given country/filter
        │                    (effectiveStatus = A, filtered by cust_Country)
        │
        ▼
  [Phase 2: Smart Fetch] ──  Collect only the UNIQUE foundation codes actually
        │                    referenced by those positions (company codes, division
        │                    codes, job codes, cost centre codes, etc.)
        │                    → Only fetch those specific records via OData
        │                    → NOT the entire foundation dataset in your SF tenant
        │
        ▼
  [Phase 3: Cache]       ──  Store everything in local SQLite — subsequent runs
        │                    can re-validate without re-fetching from SF
        │
        ▼
  [Phase 4: Rule Engine] ──  config/rules.yaml drives CHK-09 to CHK-18 (configurable)
        │                    Each rule defines the relationship to validate,
        │                    which fields to compare, and the severity if it fails
        │
        ▼
  [Phase 5: Reports]     ──  HTML (filterable) │ Excel workbook │ Console table │ run_manifest.json
```

### Why the two-phase fetch matters

Most SF API tools either fetch all foundation data (slow, unnecessary) or rely on pre-exported files (stale). This tool takes a smarter approach:

1. **Fetch positions first** — pull every active position for your target country in one paginated sweep
2. **Collect unique codes** — scan those positions and build a set of every unique Division code, Department code, Job Code, Cost Centre code, etc. that is actually referenced
3. **Fetch only what you need** — request only those specific foundation records via batched OData calls

On a large SF tenant with thousands of foundation objects, this means you might fetch 50 active Job Codes instead of 3,000 — because only those 50 are actually referenced by positions in your target country. The tool focuses validation on what matters to your project scope, not the entire global configuration.

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
   python main.py
   ```
   The tool presents an interactive menu — choose your run mode (see **Run modes** below).

---

## Run modes

The tool offers three run modes via an interactive menu, designed around a common consulting workflow:

```
╔══════════════════════════════════════════════════════╗
║       SF Position Integrity Checker  v1.0.0         ║
╠══════════════════════════════════════════════════════╣
║  [1]  Extract & Validate   (full run, hits SF API)  ║
║  [2]  Only Validate        (re-run on cached data)  ║
║  [3]  Only Extract         (fetch only, no checks)  ║
║  [0]  Exit                                          ║
╚══════════════════════════════════════════════════════╝
```

| Mode | When to use |
|------|-------------|
| **[1] Extract & Validate** | First run, or after foundation data has changed in SF. Fetches fresh data from your tenant and immediately runs all checks. |
| **[2] Only Validate** | Re-run checks against already-cached data without hitting the SF API again. Useful when you have adjusted `rules.yaml` and want to re-evaluate the same dataset instantly. |
| **[3] Only Extract** | Fetch and cache position + foundation data without running checks. Useful to pre-load data during a client call and validate offline later. |

### Country / filter parameter

When you select **[1] Extract & Validate** or **[3] Only Extract**, the tool prompts for a country code. This maps to the `cust_Country` field on Position records:

```
Enter country code [default: CA]: US
```

Common values used across SF implementations:

| Country | Code |
|---------|------|
| Canada | `CA` or `CAN` |
| United States | `US` or `USA` |
| United Kingdom | `GB` or `GBR` |
| Saudi Arabia | `SA` or `SAU` |

> **Note:** The exact code depends on how your SF tenant has been configured. Check your `cust_Country` picklist values in SF Admin if unsure.

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

## Authentication

The tool supports two authentication methods. Set `SF_AUTH_METHOD` in your `.env` to choose.

### Basic Auth (default)
Quickest to set up. Suitable for development and internal tooling.
```env
SF_AUTH_METHOD=basic
SF_USERNAME=your_api_user
SF_PASSWORD=your_password
SF_ODATA_BASE_URL=https://<tenant>.successfactors.com/odata/v2/
```

### OAuth2 SAML Bearer Token (recommended for enterprise)
More secure. No passwords stored. Tokens auto-refresh. Required by some enterprise security policies.
```env
SF_AUTH_METHOD=oauth2
SF_CLIENT_ID=your_client_id
SF_COMPANY_ID=your_company_id
SF_USER_ID=your_api_user_id
SF_TOKEN_URL=https://<tenant>.successfactors.com/oauth/token
SF_PRIVATE_KEY_PATH=/home/yourname/.sf_keys/sf_private_key.pem
SF_ODATA_BASE_URL=https://<tenant>.successfactors.com/odata/v2/
```

See [OAuth2 Setup Guide](docs/oauth2_setup.md) for step-by-step instructions.

---

## Credential setup (Basic Auth options)

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

- **Mode 2: Pre-Change Impact Analysis** — simulate a proposed foundation change (e.g. deactivate a Cost Centre, move a Sub Department) and surface every affected Position and Job Info record before the change is applied
- **Additional check types** — `not_null` rule type and custom expression checks via `rules.yaml`
- **Multi-country parallel runs** — fan out across all active countries in a single execution
- **OAuth2 SAML Bearer Token** — enterprise-grade authentication without stored passwords (see [OAuth2 Setup Guide](docs/oauth2_setup.md))
- **Web UI** — lightweight Streamlit dashboard for non-technical stakeholders

---

## Author

[Sahir Vhora](https://www.linkedin.com/in/sahir-vhora-9242439/) — SAP SuccessFactors Consultant

---

## License

[Apache 2.0](LICENSE)