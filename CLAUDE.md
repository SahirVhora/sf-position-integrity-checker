# CLAUDE.md — sf-position-integrity-checker

> Read this file before touching any code in this repo.
> It encodes patterns that must be followed in every change, addition, or fix.

---

> **Corrected 2026-08-03**: this file previously described a `checker.py`/`library.py`
> architecture, `PASS/WARN/FAIL/ERROR` severities, a `config/checks.yaml` path, a
> pandas/SQLite ban, and a 3-mode `auth/` module — none of that matches the code that
> actually exists. The section below reflects the real repo, verified by reading the
> files directly.

## What this tool does

CLI + optional web UI that validates SAP SuccessFactors Employee Central position
data integrity by calling OData v2 APIs via `sapsf_shared.SFClient`, running rules
loaded from `config/rules.yaml`, storing extracted data in a local per-country SQLite
DB, and producing HTML + Excel reports plus a remediation workflow.

Target user: SF EC consultant running pre-go-live or post-migration data audits.

---

## Repo structure (actual, top-level only)

```
sf-position-integrity-checker/
├── main.py                 ← CLI entrypoint (argparse: --country, --mode, --as-of-date)
├── web_ui.py                ← Flask web UI (recommended entry point)
├── mcp_server.py             ← MCP server exposing checks as tools
├── api_client.py             ← wraps sapsf_shared.AuthConfig / SFClient / SFClientError
├── fetchers.py                ← OData pulls per entity, builds records for the DB
├── validators.py               ← runs CHK-01..CHK-17 from config/rules.yaml
├── database.py                  ← SQLite store, one DB per country: ./data/sf_integrity_{COUNTRY}.db
├── db_inspector.py               ← read-only DB inspection helpers
├── reporters.py                    ← HTML + Excel report generation (uses pandas)
├── remediation.py                   ← remediation workflow/report
├── transformation_review.py          ← review helper
├── simulator.py                       ← synthetic-data simulator for demos/tests
├── config.py, config/rules.yaml        ← runtime config + the CHK rule definitions
├── auth/                                ← thin wrapper, delegates to sapsf_shared auth
├── templates/                            ← HTML report template(s)
└── test_*.py                              ← pytest tests, flat at repo root (no tests/ dir)
```

---

## CHK Framework — the core pattern

### Check identifier format
`CHK-NN`, currently **CHK-01 through CHK-17** (not CHK-09 — that was an early-draft
range some docstrings still quote; grep `config/rules.yaml` for the live set before
assuming a range). New checks continue the sequence. Never reuse an identifier.

### Severity values
Two values found in `config/rules.yaml` today: `CRITICAL` and `HIGH`. There is no
`PASS/WARN/FAIL/ERROR` enum in this repo — don't reintroduce it. If you need a
"the check ran fine" state, check how `validators.py` / `reporters.py` represent a
clean result before inventing a new field.

### Rules are YAML, not hardcoded
Rule definitions live in `config/rules.yaml`, loaded by `validators.py` at import
time (`_RULES_PATH`). Rule types supported: `scalar_match`, `set_membership`,
`not_null` (declared but not yet used by any rule). Never hardcode a rule directly
in Python — add/edit the YAML entry instead.

---

## OData / auth

Auth and the OData client are **not** hand-rolled here — `api_client.py` imports
`AuthConfig`, `SFClient`, `SFClientError` from `sapsf_shared` (the portfolio's shared
SDK at `_shared/`). Don't add a new HTTP client or a new auth module; extend
`api_client.py` or `sapsf_shared` instead. The `auth/` directory is a thin wrapper
around that, not a standalone 3-mode implementation.

---

## Local storage

`database.py` maintains one SQLite file per country at
`./data/sf_integrity_{COUNTRY}.db`. On an Extract run it wipes and recreates all
tables; on a Validate-only run it connects read-only and fails gracefully if the DB
is absent. Column names deliberately match SF OData field names so records can be
inserted without a remapping layer (the one documented exception is
`cust_sub_department`, aliased to `startDate`/`endDate`/`status` in `fetchers.py`
before saving). This is a **local reimplementation**, not
`sapsf_shared.snapshot.SnapshotStore` — see the portfolio root `CLAUDE.md` for why
that's true across most of the portfolio, not just here.

---

## Dependencies (actual, from `requirements.txt`)

```
requests, openpyxl, pandas, python-dotenv, flask, pyyaml, mcp
sapsf-shared @ git+https://github.com/SahirVhora/sapsf-shared.git
pytest (dev)
```

pandas **is** a dependency (used by `reporters.py`) — the old "never add pandas"
guidance in this file was wrong for the code as it exists today. If you want to
keep this CLI lightweight going forward, that's a decision to make deliberately,
not something already enforced by the code.

---

## CLI interface (`main.py`)

```bash
python main.py --country CAN --mode 1 --as-of-date 2026-08-01
```

Flags (all optional — omitting any of them drops into an interactive prompt):
- `--country` — country code (e.g. CAN, USA, IND, NLD)
- `--mode` — `1` = Extract & Validate, `2` = Only Validate, `3` = Only Extract
- `--as-of-date` — `YYYY-MM-DD`
- `--version`

Web UI: `python web_ui.py`. MCP server: `python mcp_server.py` (or
`run_mcp_server.sh`) — lists the CHK-01..CHK-17 rules with category/severity/
description, reading `config/rules.yaml`, no data access.

---

## Test standards

Tests are flat `test_*.py` files at repo root (`test_validators.py`,
`test_remediation.py`, `test_schema.py`, `test_simulator.py`,
`test_transformation_review.py`, `test_auth_basic.py`, `test_odata_escape.py`) —
there is no `tests/` subdirectory or `test_chkNN.py`-per-check convention. Run:
`pytest -v --tb=short` from the repo root.

---

## Common tasks — how to do them correctly

### Add a new check (e.g. CHK-18)
1. Add a `CHK-18` block to `config/rules.yaml`
2. Confirm `validators.py`'s generic rule engine can express it via
   `scalar_match` / `set_membership` / `not_null` — only add new Python logic in
   `validators.py` if the existing rule types can't express the check
3. Add/extend a test in `test_validators.py` covering the new rule
4. Update `README.md`'s checks table and `docs/` if present

### Fix a bug in an OData call
1. Write a failing test first
2. Fix it in `fetchers.py` or `api_client.py` (not in `validators.py`, which should
   stay focused on rule evaluation against already-fetched records)
3. Confirm the test passes and rerun the SQLite-dependent tests, since `database.py`
   expects specific column names produced by `fetchers.py`
