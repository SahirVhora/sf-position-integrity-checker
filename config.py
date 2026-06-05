"""
config.py - Load credentials and configuration for SF Position Integrity Checker.

Credential resolution order for Basic Auth (first source that provides all required values wins):
  1. .env file  (existing behaviour - unaffected for current users)
  2. OS keyring via the `keyring` library (with file-based fallback when keyring unavailable)
  3. Interactive prompt (offers to save to keyring for next time)

To store credentials in the OS keyring once:
    from config import store_credentials_to_keyring
    store_credentials_to_keyring()
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Auth method
# ---------------------------------------------------------------------------

AUTH_METHOD: str = os.getenv("SF_AUTH_METHOD", "basic").lower().strip()

if AUTH_METHOD not in ("basic", "oauth2"):
    raise ValueError(f"SF_AUTH_METHOD must be 'basic' or 'oauth2', got '{AUTH_METHOD}'")

# ---------------------------------------------------------------------------
# OAuth2 config values (loaded unconditionally so missing-var errors surface early)
# ---------------------------------------------------------------------------

OAUTH2_CLIENT_ID = os.getenv("SF_CLIENT_ID", "")
OAUTH2_COMPANY_ID = os.getenv("SF_COMPANY_ID", "")
OAUTH2_USER_ID = os.getenv("SF_USER_ID", "")
OAUTH2_TOKEN_URL = os.getenv("SF_TOKEN_URL", "")
OAUTH2_PRIVATE_KEY_PATH = os.getenv("SF_PRIVATE_KEY_PATH", "")

# ---------------------------------------------------------------------------
# Base URL resolution
# ---------------------------------------------------------------------------

_raw_url = os.environ.get("SF_ODATA_BASE_URL") or os.environ.get("SF_BASE_URL") or ""

# Normalise the URL: strip /odata/v2 suffix if accidentally included in base var,
# then reconstruct to guarantee the /odata/v2/ suffix is present exactly once.
_raw_url = _raw_url.rstrip("/")
if _raw_url.endswith("/odata/v2"):
    _raw_url = _raw_url[: -len("/odata/v2")]

SF_BASE_URL: str = _raw_url  # e.g. https://api4.successfactors.com
ODATA_BASE_URL: str = f"{SF_BASE_URL}/odata/v2/" if SF_BASE_URL else ""

# ---------------------------------------------------------------------------
# Basic Auth credential resolution (lazy - only needed in basic mode)
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

    global \
        SF_USERNAME, \
        SF_PASSWORD, \
        SF_INSTANCE_ID, \
        HEADERS, \
        SF_BASE_URL, \
        ODATA_BASE_URL
    SF_USERNAME = username
    SF_PASSWORD = password
    SF_INSTANCE_ID = instance_id
    if raw_url:
        SF_BASE_URL = raw_url
        ODATA_BASE_URL = f"{raw_url}/odata/v2/"
    HEADERS = {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# Provide sensible defaults so importing modules don't fail at import time
SF_USERNAME: str = os.environ.get("SF_USERNAME", "")
SF_PASSWORD: str = os.environ.get("SF_PASSWORD", "")
SF_INSTANCE_ID: str = (
    os.environ.get("SF_COMPANY_ID") or os.environ.get("SF_INSTANCE_ID") or ""
)
HEADERS: dict = {}

if AUTH_METHOD == "basic":
    _init_basic_auth()

# ---------------------------------------------------------------------------
# Keyring helper (public API - preserved for backward compatibility)
# ---------------------------------------------------------------------------

_KEYRING_SERVICE = "sf_position_integrity_checker"
_CREDS_FILE = os.path.join(os.path.dirname(__file__), "config", "credentials.json")


def _load_file_creds() -> dict:
    """Load credentials from the local file fallback."""
    try:
        with open(_CREDS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_file_creds(data: dict) -> None:
    """Save credentials to the local file fallback."""
    try:
        os.makedirs(os.path.dirname(_CREDS_FILE), exist_ok=True)
        with open(_CREDS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def get_saved_auth_config() -> dict:
    """Return the currently saved auth configuration for display in the web UI."""
    file_creds = _load_file_creds()

    try:
        import keyring as _kr

        auth_method = (
            _kr.get_password(_KEYRING_SERVICE, "auth_method")
            or file_creds.get("auth_method")
            or "basic"
        )
        base_url = (
            _kr.get_password(_KEYRING_SERVICE, "base_url")
            or file_creds.get("base_url")
            or ODATA_BASE_URL
            or ""
        )
        username = (
            _kr.get_password(_KEYRING_SERVICE, "username")
            or file_creds.get("username")
            or SF_USERNAME
            or ""
        )
        company_id = (
            _kr.get_password(_KEYRING_SERVICE, "company_id")
            or file_creds.get("company_id")
            or SF_INSTANCE_ID
            or ""
        )
        password = (
            _kr.get_password(_KEYRING_SERVICE, "password")
            or file_creds.get("password")
            or ""
        )
        client_id = (
            _kr.get_password(_KEYRING_SERVICE, "client_id")
            or file_creds.get("client_id")
            or OAUTH2_CLIENT_ID
            or ""
        )
        user_id = (
            _kr.get_password(_KEYRING_SERVICE, "user_id")
            or file_creds.get("user_id")
            or OAUTH2_USER_ID
            or ""
        )
        token_url = (
            _kr.get_password(_KEYRING_SERVICE, "token_url")
            or file_creds.get("token_url")
            or OAUTH2_TOKEN_URL
            or ""
        )
        pk_path = (
            _kr.get_password(_KEYRING_SERVICE, "private_key_path")
            or file_creds.get("private_key_path")
            or OAUTH2_PRIVATE_KEY_PATH
            or ""
        )
    except Exception:
        auth_method = file_creds.get("auth_method") or AUTH_METHOD
        base_url = file_creds.get("base_url") or ODATA_BASE_URL
        username = file_creds.get("username") or SF_USERNAME
        company_id = file_creds.get("company_id") or SF_INSTANCE_ID
        password = file_creds.get("password") or ""
        client_id = file_creds.get("client_id") or OAUTH2_CLIENT_ID
        user_id = file_creds.get("user_id") or OAUTH2_USER_ID
        token_url = file_creds.get("token_url") or OAUTH2_TOKEN_URL
        pk_path = file_creds.get("private_key_path") or OAUTH2_PRIVATE_KEY_PATH
    return {
        "auth_method": auth_method,
        "base_url": base_url,
        "username": username,
        "company_id": company_id,
        "password_saved": bool(password),
        "client_id": client_id,
        "user_id": user_id,
        "token_url": token_url,
        "private_key_path": pk_path,
    }


def set_basic_auth_config(
    base_url: str, username: str, password: str, company_id: str
) -> None:
    """Save Basic Auth credentials and update module-level globals for the running process."""
    keyring_ok = False
    try:
        import keyring as _kr

        _kr.set_password(_KEYRING_SERVICE, "auth_method", "basic")
        _kr.set_password(_KEYRING_SERVICE, "base_url", base_url)
        _kr.set_password(_KEYRING_SERVICE, "username", username)
        _kr.set_password(_KEYRING_SERVICE, "password", password)
        _kr.set_password(_KEYRING_SERVICE, "company_id", company_id or "")
        keyring_ok = True
    except Exception:
        pass  # keyring unavailable - fall through to file-based storage

    if not keyring_ok:
        existing = _load_file_creds()
        existing.update(
            {
                "auth_method": "basic",
                "base_url": base_url,
                "username": username,
                "password": password,
                "company_id": company_id or "",
            }
        )
        _save_file_creds(existing)

    # Override os.environ so resolve_basic_credentials() picks these up immediately
    # even if a .env file was loaded at startup with different values.
    os.environ["SF_ODATA_BASE_URL"] = base_url
    os.environ["SF_USERNAME"] = username
    os.environ["SF_PASSWORD"] = password
    os.environ["SF_COMPANY_ID"] = company_id or ""

    # Re-initialise module-level globals so the current process uses the new creds.
    _init_basic_auth()


def set_oauth2_auth_config(
    client_id: str,
    company_id: str,
    user_id: str,
    token_url: str,
    private_key_path: str,
    base_url: str,
) -> None:
    """Save OAuth2 credentials and update module-level globals for the running process."""
    keyring_ok = False
    try:
        import keyring as _kr

        _kr.set_password(_KEYRING_SERVICE, "auth_method", "oauth2")
        _kr.set_password(_KEYRING_SERVICE, "base_url", base_url)
        _kr.set_password(_KEYRING_SERVICE, "client_id", client_id)
        _kr.set_password(_KEYRING_SERVICE, "company_id", company_id)
        _kr.set_password(_KEYRING_SERVICE, "user_id", user_id)
        _kr.set_password(_KEYRING_SERVICE, "token_url", token_url)
        _kr.set_password(_KEYRING_SERVICE, "private_key_path", private_key_path)
        keyring_ok = True
    except Exception:
        pass

    if not keyring_ok:
        existing = _load_file_creds()
        existing.update(
            {
                "auth_method": "oauth2",
                "base_url": base_url,
                "client_id": client_id,
                "company_id": company_id,
                "user_id": user_id,
                "token_url": token_url,
                "private_key_path": private_key_path,
            }
        )
        _save_file_creds(existing)

    os.environ["SF_AUTH_METHOD"] = "oauth2"
    os.environ["SF_CLIENT_ID"] = client_id
    os.environ["SF_COMPANY_ID"] = company_id
    os.environ["SF_USER_ID"] = user_id
    os.environ["SF_TOKEN_URL"] = token_url
    os.environ["SF_PRIVATE_KEY_PATH"] = private_key_path

    raw_url = base_url.rstrip("/")
    if raw_url.endswith("/odata/v2"):
        raw_url = raw_url[: -len("/odata/v2")]

    global AUTH_METHOD, OAUTH2_CLIENT_ID, OAUTH2_COMPANY_ID, OAUTH2_USER_ID  # noqa: PLW0603
    global OAUTH2_TOKEN_URL, OAUTH2_PRIVATE_KEY_PATH, SF_BASE_URL, ODATA_BASE_URL  # noqa: PLW0603
    AUTH_METHOD = "oauth2"
    OAUTH2_CLIENT_ID = client_id
    OAUTH2_COMPANY_ID = company_id
    OAUTH2_USER_ID = user_id
    OAUTH2_TOKEN_URL = token_url
    OAUTH2_PRIVATE_KEY_PATH = private_key_path
    SF_BASE_URL = raw_url
    ODATA_BASE_URL = f"{raw_url}/odata/v2/" if raw_url else ""


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

    _kr.set_password(_KEYRING_SERVICE, "base_url", url)
    _kr.set_password(_KEYRING_SERVICE, "username", username)
    _kr.set_password(_KEYRING_SERVICE, "password", password)
    _kr.set_password(_KEYRING_SERVICE, "company_id", company_id)
    print("[OK] Credentials stored in OS keyring.")


def _env_file_path() -> str:
    return os.path.join(os.path.dirname(__file__), ".env")


def _write_env_var(key: str, value: str) -> None:
    os.environ[key] = value
    try:
        from dotenv import set_key

        env_path = _env_file_path()
        if not os.path.exists(env_path):
            open(env_path, "a", encoding="utf-8").close()
        set_key(env_path, key, value)
        return
    except Exception:
        pass

    env_path = _env_file_path()
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    found = False
    for idx, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[idx] = f"{key}={value}\n"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}\n")
    with open(env_path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def refresh_config() -> None:
    global AUTH_METHOD, OAUTH2_CLIENT_ID, OAUTH2_COMPANY_ID, OAUTH2_USER_ID
    global OAUTH2_TOKEN_URL, OAUTH2_PRIVATE_KEY_PATH
    global \
        SF_BASE_URL, \
        ODATA_BASE_URL, \
        SF_USERNAME, \
        SF_PASSWORD, \
        SF_INSTANCE_ID, \
        HEADERS

    AUTH_METHOD = os.getenv("SF_AUTH_METHOD", AUTH_METHOD).lower().strip()
    if AUTH_METHOD not in ("basic", "oauth2"):
        raise ValueError(
            f"SF_AUTH_METHOD must be 'basic' or 'oauth2', got '{AUTH_METHOD}'"
        )

    if AUTH_METHOD == "basic":
        _init_basic_auth()
    else:
        OAUTH2_CLIENT_ID = os.getenv("SF_CLIENT_ID", "")
        OAUTH2_COMPANY_ID = os.getenv("SF_COMPANY_ID", "")
        OAUTH2_USER_ID = os.getenv("SF_USER_ID", "")
        OAUTH2_TOKEN_URL = os.getenv("SF_TOKEN_URL", "")
        OAUTH2_PRIVATE_KEY_PATH = os.getenv("SF_PRIVATE_KEY_PATH", "")
        raw_url = (
            os.environ.get("SF_ODATA_BASE_URL") or os.environ.get("SF_BASE_URL") or ""
        )
        raw_url = raw_url.rstrip("/")
        if raw_url.endswith("/odata/v2"):
            raw_url = raw_url[: -len("/odata/v2")]
        SF_BASE_URL = raw_url
        ODATA_BASE_URL = f"{SF_BASE_URL}/odata/v2/" if SF_BASE_URL else ""
        SF_USERNAME = os.environ.get("SF_USERNAME", "")
        SF_PASSWORD = os.environ.get("SF_PASSWORD", "")
        SF_INSTANCE_ID = (
            os.environ.get("SF_COMPANY_ID") or os.environ.get("SF_INSTANCE_ID") or ""
        )
        HEADERS = {}


def get_saved_auth_config() -> dict:
    method = os.getenv("SF_AUTH_METHOD", AUTH_METHOD).lower().strip()
    auth_config = {
        "auth_method": method,
        "base_url": os.getenv("SF_ODATA_BASE_URL") or os.getenv("SF_BASE_URL") or "",
    }
    if method == "basic":
        auth_config.update(
            {
                "username": os.getenv("SF_USERNAME", ""),
                "company_id": os.getenv("SF_COMPANY_ID")
                or os.getenv("SF_INSTANCE_ID", ""),
                "password_saved": bool(os.getenv("SF_PASSWORD", "")),
            }
        )
    else:
        auth_config.update(
            {
                "client_id": os.getenv("SF_CLIENT_ID", ""),
                "company_id": os.getenv("SF_COMPANY_ID", ""),
                "user_id": os.getenv("SF_USER_ID", ""),
                "token_url": os.getenv("SF_TOKEN_URL", ""),
                "private_key_path": os.getenv("SF_PRIVATE_KEY_PATH", ""),
                "password_saved": False,
            }
        )
    return auth_config


def set_basic_auth_config(
    base_url: str,
    username: str,
    password: str,
    company_id: str = "",
) -> None:
    _write_env_var("SF_AUTH_METHOD", "basic")
    _write_env_var("SF_ODATA_BASE_URL", base_url)
    _write_env_var("SF_USERNAME", username)
    if company_id:
        _write_env_var("SF_COMPANY_ID", company_id)
    _write_env_var("SF_PASSWORD", password)
    refresh_config()


def set_oauth2_auth_config(
    client_id: str,
    company_id: str,
    user_id: str,
    token_url: str,
    private_key_path: str,
    base_url: str = "",
) -> None:
    _write_env_var("SF_AUTH_METHOD", "oauth2")
    _write_env_var("SF_CLIENT_ID", client_id)
    _write_env_var("SF_COMPANY_ID", company_id)
    _write_env_var("SF_USER_ID", user_id)
    _write_env_var("SF_TOKEN_URL", token_url)
    _write_env_var("SF_PRIVATE_KEY_PATH", private_key_path)
    if base_url:
        _write_env_var("SF_ODATA_BASE_URL", base_url)
    refresh_config()


# ---------------------------------------------------------------------------
# Pagination / retry constants
# ---------------------------------------------------------------------------

PAGE_SIZE: int = 1000
MAX_RETRIES: int = 3
