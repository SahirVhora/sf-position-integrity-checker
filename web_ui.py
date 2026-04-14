"""
web_ui.py — lightweight Flask frontend for SF Position Integrity Checker.

Run this frontend from the repository root, then open http://127.0.0.1:5000/ in a browser.
"""

import glob
import os
import re
import threading
import traceback
import uuid
from datetime import datetime

from flask import Flask, jsonify, render_template, request, send_from_directory, url_for

import config
import database
from fetchers import run_full_extract
from main import _do_validate

BASE_DIR = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

COUNTRIES = [
    ("AFG", "Afghanistan"),
    ("ALA", "Åland Islands"),
    ("ALB", "Albania"),
    ("DZA", "Algeria"),
    ("AND", "Andorra"),
    ("AGO", "Angola"),
    ("ARG", "Argentina"),
    ("ARM", "Armenia"),
    ("AUS", "Australia"),
    ("AUT", "Austria"),
    ("AZE", "Azerbaijan"),
    ("BHS", "Bahamas"),
    ("BHR", "Bahrain"),
    ("BGD", "Bangladesh"),
    ("BRB", "Barbados"),
    ("BLR", "Belarus"),
    ("BEL", "Belgium"),
    ("BLZ", "Belize"),
    ("BEN", "Benin"),
    ("BTN", "Bhutan"),
    ("BOL", "Bolivia"),
    ("BRA", "Brazil"),
    ("BRN", "Brunei"),
    ("BGR", "Bulgaria"),
    ("CAN", "Canada"),
    ("CHL", "Chile"),
    ("CHN", "China"),
    ("COL", "Colombia"),
    ("CRI", "Costa Rica"),
    ("CYP", "Cyprus"),
    ("CZE", "Czech Republic"),
    ("DNK", "Denmark"),
    ("DOM", "Dominican Republic"),
    ("ECU", "Ecuador"),
    ("EGY", "Egypt"),
    ("EST", "Estonia"),
    ("FIN", "Finland"),
    ("FRA", "France"),
    ("DEU", "Germany"),
    ("GRC", "Greece"),
    ("HKG", "Hong Kong"),
    ("HUN", "Hungary"),
    ("ISL", "Iceland"),
    ("IND", "India"),
    ("IDN", "Indonesia"),
    ("IRL", "Ireland"),
    ("ISR", "Israel"),
    ("ITA", "Italy"),
    ("JPN", "Japan"),
    ("JOR", "Jordan"),
    ("KAZ", "Kazakhstan"),
    ("KEN", "Kenya"),
    ("KOR", "South Korea"),
    ("KWT", "Kuwait"),
    ("LVA", "Latvia"),
    ("LBN", "Lebanon"),
    ("LTU", "Lithuania"),
    ("LUX", "Luxembourg"),
    ("MYS", "Malaysia"),
    ("MLT", "Malta"),
    ("MEX", "Mexico"),
    ("MCO", "Monaco"),
    ("MAR", "Morocco"),
    ("NLD", "Netherlands"),
    ("NZL", "New Zealand"),
    ("NOR", "Norway"),
    ("OMN", "Oman"),
    ("PAK", "Pakistan"),
    ("PHL", "Philippines"),
    ("POL", "Poland"),
    ("PRT", "Portugal"),
    ("QAT", "Qatar"),
    ("ROU", "Romania"),
    ("SGP", "Singapore"),
    ("ZAF", "South Africa"),
    ("ESP", "Spain"),
    ("SWE", "Sweden"),
    ("CHE", "Switzerland"),
    ("TWN", "Taiwan"),
    ("THA", "Thailand"),
    ("TUR", "Turkey"),
    ("ARE", "United Arab Emirates"),
    ("GBR", "United Kingdom"),
    ("USA", "United States"),
    ("VNM", "Vietnam"),
]

MODES = [
    ("extract_validate", "Extract & Validate"),
    ("validate_only", "Only Validate"),
    ("extract_only", "Only Extract"),
]

REPORT_PATTERN = re.compile(r"position_integrity_([A-Z]{2,4})_(\d{8})\.html$")

RUNS: dict[str, dict] = {}
RUNS_LOCK = threading.Lock()
MAX_PROGRESS_LOG = 50

app = Flask(__name__, template_folder="templates")


def _ensure_output_dir() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _saved_auth_config() -> dict:
    try:
        return config.get_saved_auth_config()
    except Exception:
        return {
            "auth_method": "basic",
            "base_url": "",
            "username": "",
            "company_id": "",
            "password_saved": False,
        }


def _new_run(country: str, mode: str) -> str:
    run_id = uuid.uuid4().hex
    with RUNS_LOCK:
        RUNS[run_id] = {
            "status": "running",
            "message": "Queued",
            "phase": None,
            "step": None,
            "current": 0,
            "total": 0,
            "events": [],
            "summary": None,
            "error": None,
            "country": country,
            "mode": mode,
        }
    return run_id


def _push_progress_event(run_id: str, event: dict) -> None:
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            return
        run["phase"] = event.get("phase")
        run["step"] = event.get("step")
        run["message"] = event.get("message", run["message"])
        run["current"] = event.get("current", run["current"])
        run["total"] = event.get("total", run["total"])
        run["events"].append(event)
        if len(run["events"]) > MAX_PROGRESS_LOG:
            run["events"].pop(0)


def _finalize_run(run_id: str, message: str, summary: dict | None = None) -> None:
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            return
        run["status"] = "done"
        run["message"] = message
        run["summary"] = summary if summary is not None else run["summary"]


def _fail_run(run_id: str, error_message: str) -> None:
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            return
        run["status"] = "error"
        run["error"] = error_message
        run["message"] = error_message


def _run_report_thread(run_id: str, country: str, mode: str) -> None:
    try:
        database.set_country(country)

        if mode == "validate_only":
            _push_progress_event(run_id, {
                "phase": "validate",
                "step": "validate",
                "message": "Running validation on cached data...",
                "status": "running",
                "current": 0,
                "total": None,
            })
            _do_validate(country)
            _finalize_run(
                run_id,
                f"Validate only complete for {country}. The HTML report has been written to the output folder.",
            )
            return

        summary = run_full_extract(
            country,
            progress_callback=lambda event: _push_progress_event(run_id, event),
        )

        if summary.get("positions", 0) == 0:
            _finalize_run(
                run_id,
                f"No active positions were found for {country}. Please verify your cust_Country values and try again.",
                summary=summary,
            )
            return

        if mode == "extract_validate":
            _push_progress_event(run_id, {
                "phase": "validate",
                "step": "validate",
                "message": "Running validation on the fresh extract...",
                "status": "running",
                "current": 0,
                "total": None,
            })
            _do_validate(country)
            status = (
                f"Extract & Validate complete for {country}. "
                "The HTML report has been written to the output folder."
            )
        else:
            status = (
                f"Extract only complete for {country}. "
                "The position and foundation data have been saved to the local database."
            )

        _finalize_run(run_id, status, summary=summary)
    except Exception as exc:
        error_message = str(exc) or "An unexpected error occurred while running the report."
        _fail_run(run_id, error_message)
        traceback.print_exc()


@app.route("/run-report", methods=["POST"])
def run_report():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form

    country = str(payload.get("country", "")).strip().upper()
    mode = str(payload.get("mode", "extract_validate"))

    if not country:
        return jsonify({"error": "Country code is required."}), 400
    if mode not in {value for value, _ in MODES}:
        return jsonify({"error": "Unsupported run mode."}), 400

    run_id = _new_run(country, mode)
    thread = threading.Thread(target=_run_report_thread, args=(run_id, country, mode), daemon=True)
    thread.start()
    return jsonify({"run_id": run_id})


@app.route("/auth-config", methods=["GET", "POST"])
def auth_config():
    if request.method == "GET":
        return jsonify(_saved_auth_config())

    payload = request.get_json(silent=True) or request.form
    auth_method = str(payload.get("auth_method", "basic")).strip().lower()

    if auth_method not in {"basic", "oauth2"}:
        return jsonify({"error": "Unsupported auth method."}), 400

    try:
        if auth_method == "basic":
            base_url = str(payload.get("base_url", "")).strip()
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", "")).strip()
            company_id = str(payload.get("company_id", "")).strip()
            saved = _saved_auth_config()

            if not base_url or not username:
                return jsonify({"error": "Base URL and username are required for Basic Auth."}), 400
            if not password:
                if not saved.get("password_saved"):
                    return jsonify({"error": "Password is required for Basic Auth."}), 400
                password = os.environ.get("SF_PASSWORD", "")
                if not password:
                    return jsonify({"error": "Saved password not available. Please enter it again."}), 400

            config.set_basic_auth_config(base_url, username, password, company_id)
        else:
            base_url = str(payload.get("base_url", "")).strip()
            client_id = str(payload.get("client_id", "")).strip()
            company_id = str(payload.get("company_id", "")).strip()
            user_id = str(payload.get("user_id", "")).strip()
            token_url = str(payload.get("token_url", "")).strip()
            private_key_path = str(payload.get("private_key_path", "")).strip()

            if not base_url or not client_id or not company_id or not user_id or not token_url or not private_key_path:
                return jsonify({"error": "All OAuth2 fields are required."}), 400

            config.set_oauth2_auth_config(
                client_id=client_id,
                company_id=company_id,
                user_id=user_id,
                token_url=token_url,
                private_key_path=private_key_path,
                base_url=base_url,
            )
    except Exception as exc:
        return jsonify({"error": str(exc) or "Unable to save auth configuration."}), 500

    return jsonify({"status": "saved", "auth_method": auth_method})


@app.route("/run-status")
def run_status():
    run_id = str(request.args.get("run_id", "")).strip()
    if not run_id:
        return jsonify({"error": "Missing run_id."}), 400

    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            return jsonify({"error": "Unknown run_id."}), 404
        result = {
            "status": run["status"],
            "message": run["message"],
            "phase": run["phase"],
            "step": run["step"],
            "current": run["current"],
            "total": run["total"],
            "events": run["events"],
            "summary": run["summary"],
            "error": run["error"],
            "country": run["country"],
            "mode": run["mode"],
        }

    if result["status"] == "done":
        result["reports"] = _report_files()

    return jsonify(result)


@app.route("/reports")
def reports_json():
    return jsonify(_report_files())


def _report_files() -> list[dict]:
    _ensure_output_dir()
    files = glob.glob(os.path.join(OUTPUT_DIR, "position_integrity_*.html"))
    result = []
    for path in sorted(files, key=os.path.getmtime, reverse=True)[:10]:
        filename = os.path.basename(path)
        match = REPORT_PATTERN.match(filename)
        if not match:
            continue
        country, run_date = match.groups()
        result.append({
            "name": filename,
            "country": country,
            "run_date": datetime.strptime(run_date, "%Y%m%d").date().isoformat(),
            "url": url_for("download_output", filename=filename),
            "related": _related_outputs(filename),
        })
    return result


def _related_outputs(html_filename: str) -> list[dict]:
    prefix, _ = os.path.splitext(html_filename)
    extensions = [".html", ".csv", ".xlsx", ".json"]
    related = []
    for ext in extensions:
        path = os.path.join(OUTPUT_DIR, f"{prefix}{ext}")
        if os.path.exists(path):
            related.append({
                "name": os.path.basename(path),
                "url": url_for("download_output", filename=os.path.basename(path)),
            })
    return related


FETCH_LABELS = [
    ("positions", "Positions fetched"),
    ("companies", "Companies"),
    ("business_units", "Business Units"),
    ("divisions", "Divisions"),
    ("departments", "Departments"),
    ("sub_departments", "Sub Departments"),
    ("job_codes", "Job Codes"),
    ("job_class_can", "Job Class Local Records"),
    ("cost_centers", "Cost Centres"),
    ("locations", "Locations"),
]


def _summary_label(key: str, country: str) -> str:
    if key == "job_class_can":
        country = (country or "CAN").strip().upper()
        return f"Job Class {country} Records"
    return dict(FETCH_LABELS).get(key, key)


def _summary_items(summary: dict, country: str) -> list[dict]:
    return [
        {"label": _summary_label(key, country), "value": summary[key]}
        for key, _ in FETCH_LABELS
        if key in summary
    ]


def _latest_report() -> dict | None:
    reports = _report_files()
    return reports[0] if reports else None


@app.route("/output/<path:filename>")
def download_output(filename: str):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=False)


@app.route("/", methods=["GET", "POST"])
def index():
    _ensure_output_dir()
    status = None
    error = None
    report_links = []
    summary = None
    form = {
        "country": "CAN",
        "mode": "extract_validate",
    }

    fetch_details = None
    if request.method == "POST":
        form["country"] = request.form.get("country", "").strip().upper()
        form["mode"] = request.form.get("mode", "extract_validate")

        if not form["country"]:
            error = "Please choose or enter a country code."
        else:
            try:
                database.set_country(form["country"])
                if form["mode"] == "extract_validate":
                    summary = run_full_extract(form["country"])
                    if summary.get("positions", 0) == 0:
                        status = (
                            f"No active positions were found for {form['country']}. "
                            "Please verify your cust_Country values and try again."
                        )
                    else:
                        fetch_details = _summary_items(summary, form["country"])
                        _do_validate(form["country"])
                        status = (
                            f"Extract & Validate complete for {form['country']}. "
                            "The HTML report has been written to the output folder."
                        )
                elif form["mode"] == "validate_only":
                    _do_validate(form["country"])
                    status = (
                        f"Validate only complete for {form['country']}. "
                        "The HTML report has been written to the output folder."
                    )
                elif form["mode"] == "extract_only":
                    summary = run_full_extract(form["country"])
                    if summary.get("positions", 0) == 0:
                        status = (
                            f"No active positions were found for {form['country']}. "
                            "Please verify your cust_Country values and try again."
                        )
                    else:
                        fetch_details = _summary_items(summary, form["country"])
                        status = (
                            f"Extract only complete for {form['country']}. "
                            "The position and foundation data have been saved to the local database."
                        )
                else:
                    error = "Unsupported run mode selected."
            except SystemExit as exc:
                error = str(exc) or "A required database or validation condition was not met."
            except Exception:
                error = (
                    "An unexpected error occurred while running the report. "
                    "Check the terminal output for details."
                )
                traceback.print_exc()

        report_links = _report_files()

    if request.method == "GET":
        report_links = _report_files()

    latest_report = _latest_report()
    auth_config = _saved_auth_config()
    return render_template(
        "index.html",
        countries=COUNTRIES,
        modes=MODES,
        reports=report_links,
        latest_report=latest_report,
        fetch_details=fetch_details,
        summary=fetch_details,
        status=status,
        error=error,
        form=form,
        auth_config=auth_config,
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
