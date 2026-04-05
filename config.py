"""
config.py — Load credentials and configuration for SF Position Integrity Checker.

Credential resolution order for Basic Auth (first source that provides all required values wins):
  1. .env file  (existing behaviour — unaffected for current users)
  2. OS keyring via the `keyring` library
  3. Interactive prompt (offers to save to keyring for next time)

To store credentials in the OS keyring once:
    from config import store_credentials_to_keyring
    store_credentials_to_keyring()
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Auth method
# ---------------------------------------------------------------------------

AUTH_METHOD: str = os.getenv("SF_AUTH_METHOD", "basic").lower().strip()

if AUTH_METHOD not in ("basic", "oauth2"):
    raise ValueError(
        f"SF_AUTH_METHOD must be 'basic' or 'oauth2', got '{AUTH_METHOD}'"
    )

# ---------------------------------------------------------------------------
# OAuth2 config values (loaded unconditionally so missing-var errors surface early)
# ---------------------------------------------------------------------------

OAUTH2_CLIENT_ID       = os.getenv("SF_CLIENT_ID", "")
OAUTH2_COMPANY_ID      = os.getenv("SF_COMPANY_ID", "")
OAUTH2_USER_ID         = os.getenv("SF_USER_ID", "")
OAUTH2_TOKEN_URL       = os.getenv("SF_TOKEN_URL", "")
OAUTH2_PRIVATE_KEY_PATH = os.getenv("SF_PRIVATE_KEY_PATH", "")

# ---------------------------------------------------------------------------
# Base URL resolution
# ---------------------------------------------------------------------------

_raw_url = (
    os.environ.get("SF_ODATA_BASE_URL")
    or os.environ.get("SF_BASE_URL")
    or ""
)

# Normalise the URL: strip /odata/v2 suffix if accidentally included in base var,
# then reconstruct to guarantee the /odata/v2/ suffix is present exactly once.
_raw_url = _raw_url.rstrip("/")
if _raw_url.endswith("/odata/v2"):
    _raw_url = _raw_url[: -len("/odata/v2")]

SF_BASE_URL: str = _raw_url  # e.g. https://api4.successfactors.com
ODATA_BASE_URL: str = f"{SF_BASE_URL}/odata/v2/" if SF_BASE_URL else ""

# ---------------------------------------------------------------------------
# Basic Auth credential resolution (lazy — only needed in basic mode)
# These module-level vars preserve backward compatibility for any code that
# imports SF_USERNAME / SF_PASSWORD / SF_INSTANCE_ID / HEADERS directly.
# ---------------------------------------------------------------------------

def _init_basic_auth():
    """Resolve Basic Auth credentials and populate module-level legacy vars."""
    from auth.basic import resolve_basic_credentials
    import base64

    raw_url, username, password, company = resolve_basic_credentials()

    # Normalise URL (same logic as above, in case it was only in keyring/prompt)
    raw_url = raw_url.rstrip("/")
    if raw_url.endswith("/odata/v2"):
        raw_url = raw_url[: -len("/odata/v2")]

    instance_id = company
    if not instance_id and "@" in username:
        instance_id = username.split("@")[-1]

    base_username = username.split("@")[0]
    credential_str = (
        f"{base_username}@{instance_id}:{password}"
        if instance_id
        else f"{base_username}:{password}"
    )
    encoded = base64.b64encode(credential_str.encode("utf-8")).decode("utf-8")

    global SF_USERNAME, SF_PASSWORD, SF_INSTANCE_ID, HEADERS, SF_BASE_URL, ODATA_BASE_URL
    SF_USERNAME    = username
    SF_PASSWORD    = password
    SF_INSTANCE_ID = instance_id
    if raw_url:
        SF_BASE_URL    = raw_url
        ODATA_BASE_URL = f"{raw_url}/odata/v2/"
    HEADERS = {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# Provide sensible defaults so importing modules don't fail at import time
SF_USERNAME:    str  = os.environ.get("SF_USERNAME", "")
SF_PASSWORD:    str  = os.environ.get("SF_PASSWORD", "")
SF_INSTANCE_ID: str  = (
    os.environ.get("SF_COMPANY_ID")
    or os.environ.get("SF_INSTANCE_ID")
    or ""
)
HEADERS: dict = {}

if AUTH_METHOD == "basic":
    _init_basic_auth()

# ---------------------------------------------------------------------------
# Keyring helper (public API — preserved for backward compatibility)
# ---------------------------------------------------------------------------

_KEYRING_SERVICE = "sf_position_integrity_checker"


def store_credentials_to_keyring(
    url=None, username=None, password=None, company_id=None
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
# Pagination / retry constants
# ---------------------------------------------------------------------------

PAGE_SIZE:   int = 1000
MAX_RETRIES: int = 3
