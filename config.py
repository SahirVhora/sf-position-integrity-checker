"""
config.py — Load credentials and build API auth for SF Position Integrity Checker.

Credential resolution order (first source that provides all required values wins):
  1. .env file  (existing behaviour — unaffected for current users)
  2. OS keyring via the `keyring` library
  3. Interactive prompt (offers to save to keyring for next time)

To store credentials in the OS keyring once:
    from config import store_credentials_to_keyring
    store_credentials_to_keyring()
"""

import base64
import os
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

_KEYRING_SERVICE = "sf_position_integrity_checker"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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

    save = input("\n  Save to OS keyring for future runs? [y/N]: ").strip().lower()
    if save == "y":
        try:
            import keyring
            keyring.set_password(_KEYRING_SERVICE, "base_url",   url)
            keyring.set_password(_KEYRING_SERVICE, "username",   username)
            keyring.set_password(_KEYRING_SERVICE, "password",   password)
            keyring.set_password(_KEYRING_SERVICE, "company_id", company)
            print("  [OK] Credentials saved to keyring.")
        except Exception as exc:
            print(f"  [WARN] Could not save to keyring: {exc}")

    return url, username, password, company


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def _resolve_credentials() -> Tuple[str, str, str, str]:
    """
    Resolve (odata_base_url, username, password, company_id) from the first
    available source: .env → keyring → interactive prompt.

    Supports both the new env var naming (SF_ODATA_BASE_URL / SF_COMPANY_ID)
    and the legacy naming (SF_BASE_URL / SF_INSTANCE_ID) so existing users
    are completely unaffected.
    """
    # --- 1. Try .env (new var names first, fall back to legacy names) ---
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

    # --- 2. Try OS keyring ---
    kr_url, kr_user, kr_pass, kr_company = _try_keyring()
    if kr_url and kr_user and kr_pass:
        return kr_url, kr_user, kr_pass, kr_company or ""

    # --- 3. Interactive prompt ---
    return _prompt_credentials()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store_credentials_to_keyring(
    url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    company_id: Optional[str] = None,
) -> None:
    """
    Store SF API credentials in the OS keyring.

    If any argument is None, the user will be prompted for it interactively.
    Call once to set up secure credential storage:

        from config import store_credentials_to_keyring
        store_credentials_to_keyring()
    """
    import getpass
    import keyring as _kr

    if url is None:
        url = input("SF OData base URL: ").strip()
    if username is None:
        username = input("SF Username: ").strip()
    if password is None:
        password = getpass.getpass("SF Password: ")
    if company_id is None:
        company_id = input("Company ID (leave blank if embedded in username): ").strip()

    _kr.set_password(_KEYRING_SERVICE, "base_url",   url)
    _kr.set_password(_KEYRING_SERVICE, "username",   username)
    _kr.set_password(_KEYRING_SERVICE, "password",   password)
    _kr.set_password(_KEYRING_SERVICE, "company_id", company_id)
    print("[OK] Credentials stored in OS keyring.")


# ---------------------------------------------------------------------------
# Module-level credential initialisation
# ---------------------------------------------------------------------------

_raw_url, SF_USERNAME, SF_PASSWORD, _company = _resolve_credentials()

# Normalise the URL: strip /odata/v2 suffix if accidentally included in base var,
# then reconstruct to guarantee the /odata/v2/ suffix is present exactly once.
_raw_url = _raw_url.rstrip("/")
if _raw_url.endswith("/odata/v2"):
    _raw_url = _raw_url[: -len("/odata/v2")]

SF_BASE_URL: str = _raw_url  # e.g. https://api4.successfactors.com

# Company/Instance ID — may be embedded in username (legacy format user@companyId)
# or supplied as a separate env var / keyring entry.
if _company:
    SF_INSTANCE_ID: str = _company
elif "@" in SF_USERNAME:
    SF_INSTANCE_ID = SF_USERNAME.split("@")[-1]
else:
    SF_INSTANCE_ID = ""

# Build Basic Auth token: Base64("{username_base}@{instance_id}:{password}")
# Strip any trailing @<companyId> from the username before re-appending.
_base_username = SF_USERNAME.split("@")[0]
_credential_str = (
    f"{_base_username}@{SF_INSTANCE_ID}:{SF_PASSWORD}"
    if SF_INSTANCE_ID
    else f"{_base_username}:{SF_PASSWORD}"
)
_encoded = base64.b64encode(_credential_str.encode("utf-8")).decode("utf-8")

HEADERS: dict = {
    "Authorization": f"Basic {_encoded}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

ODATA_BASE_URL: str = f"{SF_BASE_URL}/odata/v2/"

PAGE_SIZE: int = 1000
MAX_RETRIES: int = 3
