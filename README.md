# SF Position Integrity Checker

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)
![Platform: SAP SuccessFactors](https://img.shields.io/badge/platform-SAP%20SuccessFactors-0FAAFF)

A Python tool that validates SAP SuccessFactors **Position** object data integrity by fetching positions and foundation objects via OData v2 and running cross-entity alignment checks. Available as a **web UI** (recommended) or as a **CLI**. Built for SAP SuccessFactors consultants and HR system administrators who need to catch hierarchy, job-code, and cost-centre misalignments before they cause payroll or reporting errors.

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
| CHK-01 | Sub Department must belong to the Position's Department | CRITICAL |
| CHK-02 | Department must belong to the Position's Division | CRITICAL |
| CHK-03 | Division must be linked to the Position's Business Unit | CRITICAL |
| CHK-04 | Business Unit must be linked to the Position's Legal Entity | CRITICAL |
| CHK-05 | Cost Centre must be linked to the Position's Business Unit | CRITICAL |
| CHK-06 | Job Code Job Family must match Position's Job Family | HIGH |
| CHK-07 | Job Code Sub Family must match Position's Job Sub Family | HIGH |
| CHK-08 | Job Code Grade must match Position's Global Job Level | HIGH |
| CHK-09 | Job Code Career Path must match Position's Career Path | HIGH |

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
  [Phase 4: Rule Engine] ──  config/rules.yaml drives CHK-01 to CHK-09 (configurable)
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

### Option A — Web UI (recommended)

1. **Clone and set up**
   ```bash
   git clone https://github.com/sahirvhora/sf-position-integrity-checker.git
   cd sf-position-integrity-checker
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Start the server**
   ```bash
   python web_ui.py
   ```

3. **Open** [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser

4. **Configure credentials** — click the ⚙ gear icon in the top-right corner, enter your SF OData Base URL, Company ID, username and password, then click **Save Settings**. Credentials are validated against your SF instance on save and persisted across sessions.

5. **Run a report** — select a country code and run mode, then click **▶ Run report**. Live progress is shown in the browser. When complete, the HTML report and Excel/CSV downloads appear in the Recent Reports section.

---

### Option B — CLI

1. **Clone and set up**
   ```bash
   git clone https://github.com/sahirvhora/sf-position-integrity-checker.git
   cd sf-position-integrity-checker
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure credentials** in a `.env` file (see [Authentication](#authentication))
   ```bash
   cp .env.example .env
   # edit .env with your SF credentials
   ```

3. **Run**
   ```bash
   python main.py
   ```
   The tool presents an interactive menu — choose your run mode and country code.

---

## Run modes

| Mode | When to use |
|------|-------------|
| **Extract & Validate** | First run, or after foundation data has changed in SF. Fetches fresh data from your tenant and immediately runs all checks. |
| **Only Validate** | Re-run checks against already-cached data without hitting the SF API again. Useful when you have adjusted `rules.yaml` and want to re-evaluate the same dataset instantly. |
| **Only Extract** | Fetch and cache position + foundation data without running checks. Useful to pre-load data during a client call and validate offline later. |

### Country filter

The tool filters positions by `cust_Country`. Enter the ISO-3166 alpha-3 code used in your SF tenant:

| Country | Typical code |
|---------|------|
| Canada | `CAN` |
| United States | `USA` |
| United Kingdom | `GBR` |
| India | `IND` |
| Denmark | `DNK` |

> **Note:** The exact code depends on how your SF tenant's `cust_Country` picklist is configured. Check SF Admin if unsure.

---

## Output

After each run, the following files are written to `./output/`:

| Output | Description |
|--------|-------------|
| **HTML report** | Interactive browser report with filter dropdowns (Severity, Check ID, Category), full-text search, column sort, and one-click export. |
| **Excel workbook** | Two sheets: *Issues* (colour-coded rows by severity) and *Summary* (run statistics, SF instance, per-check breakdown). |
| **CSV** | Flat export of all issues — suitable for further analysis or loading into another tool. |
| **run_manifest.json** | Machine-readable JSON summary of the run — checks executed, issue counts, timestamp. Suitable for CI pipelines or audit trails. |

Each report permanently records the SF instance (Company ID) it was run against, so historical reports in the web UI always show the correct instance even after switching tenants.

---

## Authentication

The tool supports two authentication methods. Configure via the web UI settings modal or via `.env`.

### Basic Auth (default)

Quickest to set up. Suitable for development and internal tooling.

**Via web UI:** click the ⚙ gear icon → enter Base URL, Company ID, username, and password → Save Settings.

**Via `.env`:**
```env
SF_AUTH_METHOD=basic
SF_ODATA_BASE_URL=https://<tenant>.sapsf.eu/odata/v2/
SF_COMPANY_ID=your_company_id
SF_USERNAME=your_api_user
SF_PASSWORD=your_password
```

### OAuth2 SAML Bearer Token (recommended for enterprise)

More secure — no stored passwords, tokens auto-refresh. Required by some enterprise security policies.

```env
SF_AUTH_METHOD=oauth2
SF_ODATA_BASE_URL=https://<tenant>.sapsf.eu/odata/v2/
SF_CLIENT_ID=your_client_id
SF_COMPANY_ID=your_company_id
SF_USER_ID=your_api_user_id
SF_TOKEN_URL=https://<tenant>.sapsf.eu/oauth/token
SF_PRIVATE_KEY_PATH=/path/to/sf_private_key.pem
```

See [OAuth2 Setup Guide](docs/oauth2_setup.md) for step-by-step instructions.

---

## Credential storage

Credentials entered in the web UI are saved and reloaded automatically on server restart. The storage priority is:

1. **`.env` file** — takes precedence if populated; suitable for local dev
2. **OS keyring** — used when available (macOS Keychain, Windows Credential Manager, Linux Secret Service)
3. **`config/credentials.json`** — file-based fallback for environments without a keyring daemon (e.g. WSL2). Excluded from git via `.gitignore`.
4. **Interactive prompt** — CLI fallback only; offers to save for future runs

The web UI validates credentials against the SF instance on every save and returns a clear error if authentication fails.

---

## Customising rules

Rules are defined in `config/rules.yaml`. You can disable a check or change its severity without touching Python:

```yaml
rules:
  - id: CHK-08
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

Changes take effect immediately on the next run — no restart needed.

---

## Customising foundation objects

`config/foundation.yaml` maps SF entity names and field names to the tool's internal model. If your SF instance uses different custom field names (e.g. `cust_Division2` instead of `cust_Division`), edit this file rather than touching Python. The `fetchers.py` module reads this configuration to know which OData entities and `$select`/`$expand` parameters to request.

---

## Security

- **Credentials never leave your machine.** The tool connects only to your SF tenant and stores data in a local SQLite file under `./data/`.
- **Tenant URL is masked** in all log output and `run_manifest.json` (`***masked***` replaces the subdomain).
- **`.env`, `*.db`, and `config/credentials.json`** are in `.gitignore` and will not be committed accidentally.

---

## Running tests

An offline test suite is included — no SF credentials needed:

```bash
python test_schema.py
```

Tests cover: SQLite schema structure, CHECK constraints, date normalisation, junction table population, all integrity checks (CHK-01 to CHK-09 pass + fail cases), validation result persistence, audit SQL views, and pipe-separated junction saving.

---

## Roadmap

- **Mode 2: Pre-Change Impact Analysis** — simulate a proposed foundation change (e.g. deactivate a Cost Centre, move a Sub Department) and surface every affected Position and Job Info record before the change is applied
- **Additional check types** — `not_null` rule type and custom expression checks via `rules.yaml`
- **Multi-country parallel runs** — fan out across all active countries in a single execution

---

## Author

[Sahir Vhora](https://www.linkedin.com/in/sahir-vhora-9242439/) — SAP SuccessFactors Consultant

---

## License

[Apache 2.0](LICENSE)
