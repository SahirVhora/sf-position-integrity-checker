"""
auth/basic.py - Basic Auth header builder for SF Position Integrity Checker.

Credential resolution order:
  1. .env file
  2. OS keyring
  3. Interactive prompt
"""

import base64
import os
from typing import Optional, Tuple

_KEYRING_SERVICE = "sf_position_integrity_checker"


def _try_keyring() -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Try to load credentials from the OS keyring. Returns (url, username, password, company_id)."""
    try:
        import keyring
        url      = keyring.get_password(_KEYRING_SERVICE, "base_url")
        username = keyring.get_password(_KEYRING_SERVICE, "username")
        password = keyring.get_password(_KEYRING_SERVICE, "password")
        company  = keyring.get_password(_KEYRING_SERVICE, "company_id")
        return url, username, password, company
    except Exception:
        return None, None, None, None


def _prompt_credentials() -> Tuple[str, str, str, str]:
    """Interactively prompt for credentials and offer to save to keyring."""
    import getpass
    print("\n[CONFIG] No credentials found in .env or keyring.")
    print("         Please enter your SF API credentials:\n")
    url      = input("  SF OData base URL (e.g. https://api4.successfactors.com/odata/v2/): ").strip()
    username = input("  SF Username: ").strip()
    password = getpass.getpass("  SF Password: ")
    company  = input("  Company ID (leave blank if embedded in username): ").strip()

    save = input("\n  Save credentials for future runs? [y/N]: ").strip().lower()
    if save == "y":
        saved = False
        try:
            import keyring
            keyring.set_password(_KEYRING_SERVICE, "base_url",   url)
            keyring.set_password(_KEYRING_SERVICE, "username",   username)
            keyring.set_password(_KEYRING_SERVICE, "password",   password)
            keyring.set_password(_KEYRING_SERVICE, "company_id", company)
            print("  [OK] Credentials saved to OS keyring.")
            saved = True
        except Exception:
            pass
        if not saved:
            import json
            creds_file = os.path.join(os.path.dirname(__file__), "..", "config", "credentials.json")
            try:
                os.makedirs(os.path.dirname(creds_file), exist_ok=True)
                with open(creds_file, "w", encoding="utf-8") as f:
                    json.dump({"base_url": url, "username": username, "password": password, "company_id": company}, f, indent=2)
                print("  [OK] Credentials saved to config/credentials.json.")
            except Exception as exc:
                print(f"  [WARN] Could not save credentials: {exc}")

    return url, username, password, company


def _try_file_creds() -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Try to load credentials from the file-based fallback."""
    import json
    creds_file = os.path.join(os.path.dirname(__file__), "..", "config", "credentials.json")
    try:
        with open(creds_file, encoding="utf-8") as f:
            data = json.load(f)
        url      = data.get("base_url")
        username = data.get("username")
        password = data.get("password")
        company  = data.get("company_id")
        return url, username, password, company
    except Exception:
        return None, None, None, None


def resolve_basic_credentials() -> Tuple[str, str, str, str]:
    """
    Resolve (odata_base_url, username, password, company_id) from the first
    available source: .env → keyring → file fallback → interactive prompt.

    Supports both the new env var naming (SF_ODATA_BASE_URL / SF_COMPANY_ID)
    and the legacy naming (SF_BASE_URL / SF_INSTANCE_ID).
    """
    env_url = (
        os.environ.get("SF_ODATA_BASE_URL")
        or os.environ.get("SF_BASE_URL")
    )
    env_username = os.environ.get("SF_USERNAME")
    env_password = os.environ.get("SF_PASSWORD")
    env_company  = (
        os.environ.get("SF_COMPANY_ID")
        or os.environ.get("SF_INSTANCE_ID")
    )

    if env_url and env_username and env_password:
        return env_url, env_username, env_password, env_company or ""

    kr_url, kr_user, kr_pass, kr_company = _try_keyring()
    if kr_url and kr_user and kr_pass:
        return kr_url, kr_user, kr_pass, kr_company or ""

    fc_url, fc_user, fc_pass, fc_company = _try_file_creds()
    if fc_url and fc_user and fc_pass:
        return fc_url, fc_user, fc_pass, fc_company or ""

    return _prompt_credentials()


def get_basic_auth_headers() -> dict:
    """Return Authorization header dict for Basic Auth."""
    _, username, password, company_id = resolve_basic_credentials()

    # Strip any trailing @<companyId> from the username before re-appending.
    base_username = username.split("@")[0]

    # Derive company_id from username if not explicitly provided.
    if not company_id and "@" in username:
        company_id = username.split("@")[-1]

    credential_str = (
        f"{base_username}@{company_id}:{password}"
        if company_id
        else f"{base_username}:{password}"
    )
    encoded = base64.b64encode(credential_str.encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {encoded}"}
